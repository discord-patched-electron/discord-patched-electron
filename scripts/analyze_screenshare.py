"""Summarize existing native RTC logs without attaching to Discord or saving raw logs.

Pass rotated logs oldest first. Thread groups are observations, not connection IDs:
logs may start mid-stream, and native threads can be reused. Missing events are
unknown, not evidence that a capture/encoder/network path is healthy.
"""

import argparse
import json
import re
from pathlib import Path

HEADER = re.compile(r"^\[[\d .:-]+\] \[(\d+)\] \(([a-z_]+\.(?:cc|cpp|rs)):\d+\): (.*)$")
SOURCES = {
    "video_stream_encoder.cc",
    "resource_adaptation_processor.cc",
    "vulkan_backend.rs",
    "encoder_factory.cpp",
}
DROP = re.compile(r"Drop Frame: target bitrate (\d+), input frame rate (\d+)")
COUNTS = re.compile(
    r"Number of frames: captured (\d+), dropped \(due to congestion window pushback\) (\d+), "
    r"dropped \(due to encoder blocked\) (\d+), interval_ms (\d+)"
)


def range_sample(group, name, value):
    if name not in group:
        group[name] = {"min": value, "max": value, "last": value, "samples": 1}
    else:
        sample = group[name]
        sample.update(
            min=min(sample["min"], value),
            max=max(sample["max"], value),
            last=value,
            samples=sample["samples"] + 1,
        )


def analyze(lines):
    groups = {}
    for line in lines:
        match = HEADER.fullmatch(line.rstrip("\r\n"))
        if not match:
            continue
        thread, source, message = match.groups()
        if source not in SOURCES:
            continue
        # Output is constructed only from fixed labels, booleans and numbers.
        # Never serialize a native message, identifier, path or arbitrary string.
        group = groups.setdefault(thread, {"content_types": [], "events": {}})
        events = group["events"]
        if source == "video_stream_encoder.cc":
            content = re.search(r"content_type: (kScreenshare|kRealtimeVideo)\b", message)
            if content and content[1] not in group["content_types"]:
                group["content_types"].append(content[1])
            if "Encoder info changed to EncoderInfo" in message:
                for key in (
                    "is_hardware_accelerated",
                    "supports_native_handle",
                    "has_trusted_rate_controller",
                ):
                    value = re.search(rf"\b{key} = ([01])\b", message)
                    if value:
                        group[key] = value[1] == "1"
            drop = DROP.fullmatch(message)
            if drop:
                events["bitrate_frame_drops"] = events.get("bitrate_frame_drops", 0) + 1
                range_sample(group, "drop_target_bps", int(drop[1]))
                range_sample(group, "drop_input_fps", int(drop[2]))
            if message == "Incoming frame dropped due to that the encoder is blocked.":
                events["encoder_blocked_messages"] = events.get("encoder_blocked_messages", 0) + 1
            counts = COUNTS.fullmatch(message)
            if counts:
                captured, congestion, blocked, interval = map(int, counts.groups())
                for key, value in zip(
                    (
                        "interval_captured",
                        "interval_congestion_drops",
                        "interval_encoder_blocked",
                        "interval_ms",
                    ),
                    (captured, congestion, blocked, interval),
                    strict=True,
                ):
                    events[key] = events.get(key, 0) + value
                if interval:
                    range_sample(
                        group, "interval_capture_fps", round(captured * 1000 / interval, 3)
                    )
            if "[VSE] ReconfigureEncoder [encoder_config=" in message:
                for key in (
                    "max_framerate",
                    "min_bitrate_bps",
                    "target_bitrate_bps",
                    "max_bitrate_bps",
                ):
                    # Multiple simulcast layers are kept as samples, not summed.
                    for value in re.findall(rf"\b{key}:\s*(\d+)", message):
                        range_sample(group, "configured_" + key, int(value))
            if message.startswith("[VSE] ReconfigureEncoder: simulcast streams:"):
                # These are the effective limits after native encoder-policy
                # clamping, not the application's requested limits above.
                streams = message.split(", spatial layers:", 1)[0]
                for key in ("min", "target", "max"):
                    for value in re.findall(rf"\b{key}_kbps:\s*(\d+)", streams):
                        range_sample(group, "effective_" + key + "_bitrate_bps", int(value) * 1000)
        elif source == "resource_adaptation_processor.cc":
            adaptation = re.fullmatch(
                r'Resource "(QualityScalerResource|EncoderUsageResource)" signalled '
                r"k(Overuse|Underuse)\. Adapted (down|up) successfully\..*",
                message,
            )
            if adaptation:
                key = adaptation[1] + "_" + adaptation[3]
                events[key] = events.get(key, 0) + 1
        elif source == "vulkan_backend.rs" and "[vaapi_media_encoding::vulkan_backend]" in message:
            for fragment, key in (
                ("Creating Vulkan VAAPI encoder with options:", "vaapi_encoder_starts"),
                ("Creating CPU surface pool for", "cpu_surface_pool_creations"),
                ("Creating upload buffer for", "cpu_upload_buffer_creations"),
            ):
                if fragment in message:
                    events[key] = events.get(key, 0) + 1
        elif source == "encoder_factory.cpp" and message.startswith("Creating video encoder for: "):
            measurement = re.search(r"(?:[ {])video-quality-measurement=([^ }\s]+)", message)
            if measurement:
                events["quality_measurement_configurations"] = (
                    events.get("quality_measurement_configurations", 0) + 1
                )
                for token, key in (
                    ("imageQualityVmaf_v061", "vmaf_measurement_configurations"),
                    ("imageQualityWebrtcPsnrDb", "psnr_measurement_configurations"),
                ):
                    if re.search(rf"(?:^|,){token}:\d+(?:,|$)", measurement[1]):
                        events[key] = events.get(key, 0) + 1
    observations = [g for g in groups.values() if g["events"] or len(g) > 2 or g["content_types"]]
    return {
        "schema": 1,
        "scope": "native encoder thread observations; not end-to-end or per-connection measurements",
        "observations": observations,
        "limitations": [
            "Rotated logs must be supplied oldest first from one process lifetime.",
            "Unknown or mixed content types cannot be attributed to a screenshare.",
            "Frame-drop messages and interval counters overlap; do not add them.",
            "Capture counts are not transmitted or viewer-decoded frame counts.",
            "Bitrate drops do not alone distinguish network congestion from encoder overshoot.",
            "A native handle or DMA-BUF capability does not prove end-to-end zero-copy.",
            "Quality-measurement configurations do not count scored frames or measure their cost.",
        ],
    }


def read_logs(paths):
    for path in paths:
        # Read only the initial size, even if the live process keeps appending.
        with path.open("rb") as handle:
            remaining = handle.seek(0, 2)
            handle.seek(0)
            oversized = False
            while remaining:
                line = handle.readline(min(remaining, 65537))
                if not line:
                    break
                remaining -= len(line)
                oversized = oversized or len(line) > 65536
                if not oversized and line.endswith(b"\n"):
                    yield line.decode("utf-8", errors="replace")
                if line.endswith(b"\n"):
                    oversized = False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", nargs="+", type=Path)
    parser.add_argument(
        "--output", type=Path, help="New sanitized JSON file; never overwrites a file"
    )
    args = parser.parse_args()
    report = json.dumps(analyze(read_logs(args.logs)), indent=2) + "\n"
    if args.output:
        with args.output.open("x") as handle:
            handle.write(report)
    else:
        print(report, end="")


if __name__ == "__main__":
    main()
