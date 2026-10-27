"""Check native Wayland selection, GPU rendering and synthetic video playback."""

import argparse
import os
import shutil
import subprocess
from pathlib import Path

from common import ROOT, read_json, run, sha256, write_json

VIDEO_CODECS = {
    "h264": ("mp4", ["-c:v", "libx264", "-movflags", "+faststart"]),
    "vp8": ("webm", ["-c:v", "libvpx", "-deadline", "realtime", "-cpu-used", "8"]),
    "vp9": ("webm", ["-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8"]),
    "av1": ("webm", ["-c:v", "libaom-av1", "-cpu-used", "8", "-crf", "40"]),
    "hevc": (
        "mp4",
        [
            "-c:v",
            "libx265",
            "-preset",
            "ultrafast",
            "-x265-params",
            "pools=2:frame-threads=2",
            "-tag:v",
            "hvc1",
            "-movflags",
            "+faststart",
        ],
    ),
}


def check(
    runtime: Path,
    output: Path,
    extra: list[str],
    openasar_source: Path | None = None,
    preset: str = "perf",
    video_codec: str = "h264",
    launcher_codecs: bool = False,
    pixel_format: str = "yuv420p",
    playback_only: bool = False,
):
    runtime, output = runtime.resolve(), output.resolve()
    if output.exists() or output.is_relative_to(runtime.parent):
        raise ValueError("Choose a fresh output outside the input distribution")
    output.mkdir(parents=True)
    run("cp", "-a", "--reflink=auto", runtime.parent, output / "runtime")
    fixture = output / "fixture"
    shutil.copytree(ROOT / "tests/graphics", fixture)
    if openasar_source:
        shutil.copy2(openasar_source, fixture / "cmdSwitches.js")
        adapters = fixture / "integration/updater"
        adapters.mkdir(parents=True)
        for name in ("patches.cjs", "flags.cjs"):
            shutil.copy2(ROOT / "integration/updater" / name, adapters / name)
    extension, codec_flags = VIDEO_CODECS[video_codec]
    clip = "clip." + extension
    write_json(fixture / "clip.json", {"file": clip, "roundTrips": not playback_only})
    run(
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=1280x720:rate=30",
        "-t",
        "2",
        *codec_flags,
        "-threads",
        "2",
        "-pix_fmt",
        pixel_format,
        fixture / clip,
    )
    # Preserve the encoded profile/bit depth, not just the requested encoder flags.
    probe = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(fixture / clip)],
        text=True,
    )
    (output / "clip-probe.json").write_text(probe)
    run(ROOT / "node_modules/.bin/asar", "pack", fixture, output / "runtime/resources/app.asar")
    env = {
        **os.environ,
        "DPE_GRAPHICS_OUTPUT": str(output),
        "DPE_GRAPHICS_PRESET": preset,
        "WAYLAND_DEBUG": "client",
    }
    env.pop("ELECTRON_RUN_AS_NODE", None)
    command = [str(output / "runtime" / runtime.name), "--disable-setuid-sandbox", *extra]
    if launcher_codecs:
        command = [
            "bash",
            "-c",
            'source "$1"; shift; dpe_restore_codec_environment; dpe_preload_openh264 "$@"; exec "$@"',
            "codec-probe",
            str(ROOT / "integration/launcher/codec-preload.sh"),
            *command,
        ]
    with (output / "console.log").open("w") as log:
        process = subprocess.run(
            command,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=45,
        )
    report = read_json(output / "graphics.json")
    report.update(
        executable_sha256=sha256(runtime),
        process_exit=process.returncode,
        native_wayland_connection="wl_display"
        in (output / "console.log").read_text(errors="replace"),
        additional_flags=extra,
        video_codec=video_codec,
        launcher_codecs=launcher_codecs,
        pixel_format=pixel_format,
        playback_only=playback_only,
    )
    if openasar_source:
        report.update(openasar_source_sha256=sha256(openasar_source), openasar_preset=preset)
    report["passed"] = report.get("passed") is True and process.returncode == 0
    write_json(output / "graphics.json", report)
    print(
        {
            key: report.get(key)
            for key in (
                "passed",
                "native_wayland_connection",
                "gpuFeatures",
                "rendering",
                "players",
            )
        }
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runtime", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--flag", action="append", default=[])
    parser.add_argument("--video-codec", choices=VIDEO_CODECS, default="h264")
    parser.add_argument("--pixel-format", choices=("yuv420p", "yuv420p10le"), default="yuv420p")
    parser.add_argument(
        "--playback-only",
        action="store_true",
        help="Test clip playback and seeking without initializing video encoders",
    )
    parser.add_argument(
        "--launcher-codecs",
        action="store_true",
        help="Apply the production launcher's pre-sandbox codec loading",
    )
    parser.add_argument(
        "--strict-codecs",
        action="store_true",
        help="Also fail on errors in explicitly preferred hardware/software decoder paths",
    )
    parser.add_argument(
        "--openasar-source",
        type=Path,
        help="Apply a pinned bootstrap/src/cmdSwitches.js through the current compatibility adapter",
    )
    parser.add_argument("--preset", choices=("perf", "battery", "balanced"), default="perf")
    args = parser.parse_args()
    if args.preset != "perf" and not args.openasar_source:
        parser.error("--preset requires --openasar-source")
    if args.playback_only and args.strict_codecs:
        parser.error("--strict-codecs needs the encode/decode round trips")
    report = check(
        args.runtime,
        args.output,
        args.flag,
        args.openasar_source,
        args.preset,
        args.video_codec,
        launcher_codecs=args.launcher_codecs,
        pixel_format=args.pixel_format,
        playback_only=args.playback_only,
    )
    if not report["passed"] or (args.strict_codecs and not report["codecPreferencesPassed"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
