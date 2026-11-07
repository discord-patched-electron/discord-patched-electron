"""Exercise native screen video on a private desktop and loopback network.

Uses an existing runtime and native module, copied into a fresh output directory.
No account, camera, microphone or external network is used. The optional
Wayland receiver display uses only a hidden synthetic window on the host
compositor; capture remains on the private synthetic PipeWire stream.
The default has no GPU access. Hardware tests require an explicit --render-node.
X11 capture uses a private desktop; PipeWire uses a synthetic producer and a
private test portal. Neither measures the host compositor's capture costs by default. Explicit
--pipewire-socket/--pipewire-node inputs can instead connect an owned synthetic
workload through a real capture producer; the receiver remains isolated.
"""

import argparse
import json
import math
import os
import re
import shutil
import signal
import stat
import subprocess
from pathlib import Path

from analyze_screenshare import analyze, read_logs
from build_camera_support import build as build_capture_helper
from build_capture_fixture import build as build_capture_delay_fixture
from capture_picker_scope import write_scope as write_capture_picker_scope
from capture_scale_scope import write_scope as write_capture_scale_scope
from capture_scope import write_scope as write_capture_scope
from capture_sync_scope import write_scope as write_capture_sync_scope
from capture_upstream_scope import write_scope as write_capture_upstream_scope
from common import ROOT, sha256, write_json
from encoder_scope import write_scope as write_encoder_scope
from screenshare_receiver_reports import receiver_reports

RESOLUTIONS = (
    "640x360",
    "640x480",
    "854x480",
    "1280x720",
    "1920x1080",
    "1920x1088",
    "2560x1440",
    "3840x2160",
    "1280x1024",
    "3440x1440",
    "1918x1078",
    "1919x1079",
)


def display_device(display_backend, capture_backend):
    if display_backend == "x11":
        return []
    if display_backend != "wayland" or capture_backend != "pipewire":
        raise ValueError("Host Wayland display is restricted to synthetic PipeWire capture")
    display = os.environ.get("WAYLAND_DISPLAY", "")
    if not display:
        raise ValueError("Wayland receiver testing needs an existing Wayland socket")
    socket = Path(display)
    if not socket.is_absolute():
        socket = Path(os.environ.get("XDG_RUNTIME_DIR", "/nonexistent")) / socket
    socket = socket.resolve()
    if not stat.S_ISSOCK(socket.stat().st_mode):
        raise ValueError("Wayland display is not a socket")
    return ["--ro-bind", str(socket), "/run/dpe-screen-test-wayland"]


def render_device(path):
    if path is None:
        return []
    path = path.resolve()
    if not re.fullmatch(r"/dev/dri/renderD\d+", str(path)) or not stat.S_ISCHR(path.stat().st_mode):
        raise ValueError("Hardware tests require a /dev/dri/renderD* character device")
    return ["--dev-bind", str(path), str(path)]


def external_pipewire(socket_path, node, backend):
    """Expose one explicitly selected test producer to the isolated receiver."""
    if socket_path is None and node is None:
        return []
    if backend != "pipewire" or socket_path is None or type(node) is not int or node <= 0:
        raise ValueError("An external test producer needs both a PipeWire socket and node ID")
    socket_path = socket_path.resolve()
    if not stat.S_ISSOCK(socket_path.stat().st_mode):
        raise ValueError("The external PipeWire remote must be a Unix socket")
    return ["--ro-bind", str(socket_path), "/run/dpe-external-pipewire"]


def pipeline_evidence(lines):
    return {
        "gpu_rendition_fallbacks_in_retained_log": sum(
            "Failed to find rendition for requested resolution " in line for line in lines
        ),
        "native_frame_dimensions": sorted(
            {
                match.group(1)
                for line in lines
                if (
                    match := re.search(
                        r"Video frame parameters changed: dimensions=(\d+x\d+)", line
                    )
                )
            }
        ),
        "gpu_import_observed": any(
            "Successfully selected GPU for DMABUF:" in line for line in lines
        ),
    }


def validate_report(report, observations):
    config = report["config"]
    screenshare = [g for g in observations if g["content_types"] == ["kScreenshare"]]
    encoded = [
        s["sent"]
        for s in report.get("samples", [])
        if (s.get("sent") or {}).get("framesEncoded", 0) > 0
    ]
    expected_codecs = {
        "h264-vaapi": {"H264"},
        "h265-vaapi": {"H265"},
        "av1-vaapi": {"AV1", "AV1X"},
        "vp8": {"VP8"},
    }[config["encoder"]]
    if config.get("fallback_h264"):
        expected_codecs |= {"H264"}
    codec_verified = bool(encoded) and all(s.get("codecName") in expected_codecs for s in encoded)
    backend_verified = codec_verified
    if config["encoder"] in ("h264-vaapi", "h265-vaapi", "av1-vaapi"):
        backend_verified = (
            codec_verified
            and any(g.get("is_hardware_accelerated") is True for g in screenshare)
            and all("vaapi" in s.get("encoderImplementationName", "").lower() for s in encoded)
        )
    measurements = report.get("measurements", {})
    unique = sum(phase.get("uniqueFrames", 0) for phase in measurements.get("phases", {}).values())
    valid = unique + measurements.get("duplicates", 0)
    total = valid + measurements.get("invalidMarkers", 0) + measurements.get("unknownMarkers", 0)
    marker_coverage = valid / total if total else 0
    adapter = report.get("captureAdapter") or {}
    minimum_frames = min(
        30, max(5, math.ceil(config.get("fps", 30) * (config.get("seconds", 6) - 2) / 2))
    )
    capture_verified = True
    direct = config.get("capture_transport") == "direct-gpu"
    if config.get("backend") == "pipewire" and not direct:
        capture_verified = report.get("pipewire_streaming_observed") is True
    if direct:
        counters = report.get("captureDirect") or {}
        capture_verified = (
            counters.get("transport") == "direct-gpu"
            and counters.get("convertedFrames", 0) >= minimum_frames
            and counters.get("receivedFrames", 0) >= counters.get("convertedFrames", 0)
            and counters.get("failures") == 0
            and counters.get("activeCaptures") == 0
            and not adapter.get("frames", 0)
            and report.get("pipewire_streaming_observed") is False
            and (
                config["encoder"] == "vp8"
                or (
                    counters.get("cpuReadbacks") == 0
                    and counters.get("gpuEncoderInputs", 0) >= minimum_frames
                )
            )
        )
    if config.get("capture_mode", "off") != "off" and not direct:
        memory = "dmabufFrames" if config.get("memory") == "dmabuf" else "memfdFrames"
        capture_verified = (
            capture_verified
            and adapter.get("installed") is True
            and adapter.get(memory, 0) >= minimum_frames
            and adapter.get(memory) == adapter.get("frames")
            and not adapter.get("returnErrors", 0)
            and (
                config["capture_mode"] not in ("latest", "paced")
                or adapter.get("latestEnabled") is True
            )
        )
    validation = {
        "native_screenshare_observed": bool(screenshare),
        "codec_verified": codec_verified,
        "backend_verified": backend_verified,
        "delivery_verified": report.get("passed") is True,
        "capture_verified": capture_verified,
        "timing_verified": (
            unique >= (1 if config.get("motion") == "static" else minimum_frames)
            and not measurements.get("droppedMeasurements", 0)
            and marker_coverage >= 0.95
        ),
    }
    padded = False
    padded_sizes = set()
    maximum_width = config.get("encode_width", config.get("width", 0))
    maximum_height = config.get("encode_height", config.get("height", 0))
    if maximum_width and maximum_height:
        dimensions = [
            tuple(map(int, dimension.split("x")))
            for phase in measurements.get("phases", {}).values()
            for dimension in phase.get("dimensions", [])
        ]
        # A timing marker can survive unwanted codec padding. Delivery alone
        # must not turn an oversized decoded picture into a successful check.
        # Smaller dimensions remain valid for explicit adaptation experiments;
        # --expect-resolution separately requires an exact, stable rendition.
        padding = report.get("av1Padding") or {}
        guard = report.get("encoderGuard") or {}
        padded = (
            config["encoder"] == "av1-vaapi"
            and (
                guard.get("hardwareScope") == "amd-vcn4"
                or (guard.get("av1Alignment") or {}).get("hardwareScope") == "amd-vcn4"
            )
            and padding.get("checked", 0) >= minimum_frames
            and padding.get("failed", 0) == 0
        )
        aligned = ((maximum_width + 63) & ~63, (maximum_height + 15) & ~15)
        padded_sizes = {aligned}
        if maximum_width % 64 == 0 and maximum_height % 16 == 8:
            padded_sizes.add((maximum_width, maximum_height + 2))
        validation["framing_integrity_verified"] = bool(dimensions) and all(
            (0 < width <= maximum_width and 0 < height <= maximum_height)
            or (padded and (width, height) in padded_sizes)
            for width, height in dimensions
        )
    if config.get("sync_timeline"):
        sync = report.get("syncProducer") or {}
        validation["explicit_sync_verified"] = (
            adapter.get("syncTimelineFrames", 0) >= minimum_frames
            and adapter.get("syncTimelineFrames") == adapter.get("frames")
            and adapter.get("syncTimelineInvalidFrames") == 0
            and adapter.get("noSyncTimelineFrames") == 0
            and report.get("native_explicit_sync_observed") is True
            and sync.get("acquired", 0) >= minimum_frames
            and sync.get("released", 0) >= minimum_frames - 8
            and 0 <= sync.get("queued", 0) - sync.get("acquired", 0) <= 1
            and sync.get("delay_ms") == config.get("sync_delay_ms", 0)
        )
    if config.get("decoder"):
        decoded = [
            sample["received"]
            for sample in report.get("samples", [])
            if (sample.get("received") or {}).get("framesDecoded", 0) > 0
        ]
        implementations = {sample.get("decoderImplementationName", "") for sample in decoded}
        report["decoder_implementations"] = sorted(implementations)
        # The pinned Electron interface uses Chromium GPU decoder factories.
        # Require actual decoded frames with the current implementation name,
        # not the initial factory's hardware label before possible fallback.
        validation["decoder_verified"] = bool(decoded) and (
            implementations == {"electron"}
            if config["decoder"] == "electron"
            else all(
                re.search(r"dav1d|aom|vpx|ffmpeg|openh264", name, re.I) for name in implementations
            )
        )
    if config.get("expect_resolution"):
        dimensions = {
            dimension
            for phase in measurements.get("phases", {}).values()
            for dimension in phase.get("dimensions", [])
        }
        validation["dimensions_verified"] = dimensions == {config["expect_resolution"]} or (
            padded
            and config["expect_resolution"] == f"{maximum_width}x{maximum_height}"
            and len(dimensions) == 1
            and all(tuple(map(int, size.split("x"))) in padded_sizes for size in dimensions)
        )
    if config.get("content_pattern") == "framing":
        edges = report.get("pictureEdges") or {}
        validation["full_picture_edges_verified"] = (
            edges.get("checked", 0) >= minimum_frames and edges.get("failed", 0) == 0
        )
    if config.get("encoder_guard"):
        validation["encoder_guard_verified"] = (report.get("encoderGuard") or {}).get(
            "installed"
        ) is True
    if config.get("capture_scale"):
        scale = report.get("captureScale") or {}
        validation["capture_scale_verified"] = (
            scale.get("installed") is True
            and scale.get("calls", 0) > 0
            and scale.get("invalidWants") == 0
        )
    if config.get("capture_sync"):
        sync = report.get("captureSync") or {}
        validation["capture_sync_verified"] = (
            sync.get("installed") is True
            and sync.get("clockFailures") == 0
            and sync.get("waitErrors") == 0
            and (not config.get("sync_timeline") or sync.get("correctedWaits", 0) >= minimum_frames)
        )
    if config.get("capture_delivery", "normal") != "normal":
        delivery = report.get("captureDelivery") or []
        expected_fast = config["capture_delivery"] == "fast"
        validation["capture_delivery_verified"] = bool(delivery) and all(
            entry.get("fast") is expected_fast
            and entry.get("requestedFps") == config.get("fps")
            and entry.get("forwarded", 0) > 0
            and entry.get("callbacks", 0) == entry.get("forwarded", 0) + entry.get("dropped", 0)
            for entry in delivery
        )
    if config.get("capture_picker"):
        events = report.get("capturePicker") or []
        validation["capture_picker_verified"] = all(
            any(event.get("event") == kind for event in events)
            for kind in ("request", "started", "stopped")
        )
        cancelled = {
            event.get("requestId") for event in events if event.get("event") == "cancelled"
        }
        started = {event.get("requestId") for event in events if event.get("event") == "started"}
        validation["unconsumed_selection_cancelled"] = bool(cancelled) and not cancelled & started
    if config.get("fallback_h264"):
        guard = report.get("encoderGuard") or {}
        validation["fallback_verified"] = (
            guard.get("rejectedCreates", 0) + guard.get("rejectedUpdates", 0) > 0
            and any(
                event.get("kind") == "encoder" and event.get("codec") in {"AV1", "AV1X"}
                for event in report.get("codecFallbacks", [])
            )
            and any(
                choice.get("codec") == "H264" and choice.get("payloadType") == 103
                for choice in report.get("negotiation", [])
            )
            and bool(encoded)
            and encoded[-1].get("codecName") == "H264"
        )
    if config.get("transport_adapter"):
        transport = report.get("transportAdapter") or {}
        active = transport.get("active") or {}
        counters = active.get("captureCounters") or {}
        validation["transport_verified"] = (
            active.get("active") is True
            and active.get("actualCodec") in expected_codecs
            and bool(encoded)
            and active.get("actualCodec") == encoded[-1].get("codecName")
            and active.get("requestedFps") == config["fps"]
            and active.get("hardware") == ("software" if config["encoder"] == "vp8" else "hardware")
            and (active.get("inputFps") or 0) > 0
            and (active.get("outputFps") or 0) > 0
            and (active.get("bitrate") or 0) > 0
            and active.get("captureMethod") == ("direct-gpu" if direct else config["backend"])
            and transport.get("idle") == {"active": False}
            and (
                (direct and counters.get("frames", 0) > 0 and counters.get("mode") == "Direct GPU")
                or config["backend"] != "pipewire"
                or (
                    not direct
                    and counters.get("frames", 0) > 0
                    and counters.get("targetFps") == config["fps"]
                    and adapter.get("pacingFps") == 0
                    and (
                        config.get("external_pipewire_node")
                        or config.get("source_fps", 0) <= config["fps"]
                        or counters.get("paced", 0) > 0
                        # Explicit-sync buffers deliberately bypass the frame
                        # gate: native must consume their acquire/release lease.
                        or (
                            config.get("sync_timeline")
                            and counters.get("syncTimelineFrames", 0) > 0
                            and adapter.get("pacingBypassFrames", 0) > 0
                        )
                    )
                )
            )
            and (
                not config.get("fallback_h264")
                or ((active.get("fallbackCount") or 0) > 0 and bool(active.get("fallbackReason")))
            )
        )
    if config.get("network_profile") == "bottleneck":
        network = report.get("network") or {}
        phases = network.get("phases") or {}
        validation["network_bottleneck_exercised"] = (
            network.get("senderIdentified") is True
            and network.get("receiverIdentified") is True
            and network.get("reverseFeedbackPackets", 0) > 0
            and all(
                (phases.get(phase) or {}).get("deliveredPackets", 0) > 0
                for phase in ("initial", "constrained", "recovery")
            )
            and (phases.get("constrained") or {}).get("droppedPackets", 0) > 0
            and 0 < network.get("maximumQueuedBytes", 0) <= 256 * 1024
            and 0 < network.get("maximumQueueDelayMs", 0) <= 100
            and isinstance(network.get("maximumDeliveryLatenessMs"), (int, float))
            and 0 <= network["maximumDeliveryLatenessMs"] <= 25
            and network.get("closed") is True
            and network.get("queuedPackets") == 0
            and network.get("queuedBytes") == 0
        )
    report["marker_coverage"] = marker_coverage
    report.update(validation=validation, encoder_observations=observations)
    report["native_screenshare_observed"] = bool(screenshare)
    report["passed"] = all(validation.values())
    # A passed fixture verifies setup and delivery. Smoothness/quality and
    # recovery must be assessed from the samples, including actual dimensions.
    report["performance_improvement_verified"] = False
    return report


def check(
    runtime,
    voice,
    output,
    source,
    motion,
    fps,
    seconds,
    drop_video,
    encoder="vp8",
    resolution="640x360",
    bitrate=1000000,
    render_node=None,
    scenario="steady",
    source_fps=None,
    backend="x11",
    capture_mode="off",
    stall_ms=0,
    stall_target="capture",
    memory="memfd",
    openh264=None,
    pressure="none",
    aligned_output_control=False,
    decoder="software",
    quality_mode="fixed",
    expect_resolution=None,
    encode_resolution=None,
    display_backend="x11",
    decoder_log=False,
    encoder_guard=False,
    fallback_h264=False,
    record_packets=False,
    transport_adapter=False,
    degradation="production",
    content_pattern="tiles",
    transport_profile="production",
    capture_scale=False,
    settle_after=0,
    reconfigure_after=0,
    network_profile="none",
    sync_timeline=False,
    sync_delay_ms=0,
    capture_sync=False,
    capture_picker=False,
    capture_delivery="normal",
    capture_transport="pipewire",
    pipewire_socket=None,
    pipewire_node=None,
    receiver_bitrate=None,
    profile_pipeline=False,
    capture_priority="inherit",
    split_peers=False,
    baseline_seconds=0,
):
    runtime, voice, output = runtime.resolve(), voice.resolve(), output.resolve()
    if capture_priority not in ("inherit", "balanced"):
        raise ValueError("Unknown capture thread priority")
    if (
        type(baseline_seconds) is not int
        or not 0 <= baseline_seconds <= 10
        or (baseline_seconds and not split_peers)
    ):
        raise ValueError("Idle baseline requires split peers and 0–10 seconds")
    if split_peers and (
        backend != "pipewire"
        or network_profile != "none"
        or (
            encoder == "av1-vaapi"
            and any(
                n % align
                for n, align in zip(
                    map(int, (encode_resolution or resolution).split("x")), (64, 16), strict=True
                )
            )
        )
    ):
        raise ValueError(
            "Split peers require shared-clock GPU input, no network transitions and aligned AV1 output"
        )
    if capture_transport not in ("pipewire", "direct-gpu") or (
        capture_transport == "direct-gpu"
        and (
            backend != "pipewire"
            or not capture_picker
            or pipewire_node != 0xFFFFFFFE
            or capture_delivery != "normal"
        )
    ):
        raise ValueError(
            "Direct capture requires its selected descriptor endpoint and callback adapter"
        )
    if receiver_bitrate is not None and (
        type(receiver_bitrate) is not int or not 16000 <= receiver_bitrate <= bitrate
    ):
        raise ValueError("Receiver bitrate must be a positive allowance within the stream ceiling")
    device_arguments = render_device(render_node)
    display_arguments = display_device(display_backend, backend)
    external_arguments = external_pipewire(pipewire_socket, pipewire_node, backend)
    if (
        not isinstance(sync_timeline, bool)
        or type(sync_delay_ms) is not int
        or not 0 <= sync_delay_ms <= 100
    ):
        raise ValueError("Choose a bounded explicit-sync delay from 0 through 100 ms")
    if (sync_timeline and (backend != "pipewire" or memory != "dmabuf")) or (
        sync_delay_ms and not sync_timeline
    ):
        raise ValueError("Explicit synchronization requires the private GPU PipeWire producer")
    if degradation not in ("production", "balanced"):
        raise ValueError("Unknown screenshare degradation policy")
    if transport_profile not in ("production", "fixed"):
        raise ValueError("Unknown transport profile")
    if network_profile not in ("none", "bottleneck"):
        raise ValueError("Unknown synthetic network profile")
    if network_profile != "none" and (scenario != "steady" or not 18 <= seconds <= 60):
        raise ValueError("Network bottleneck needs a steady 18–60 second fixture")
    if seconds > 30 and network_profile == "none":
        raise ValueError("Only bounded network recovery checks may exceed 30 seconds")
    if (
        type(reconfigure_after) not in (int, float)
        or not math.isfinite(reconfigure_after)
        or reconfigure_after < 0
        or reconfigure_after >= seconds
    ):
        raise ValueError("Choose a reconfiguration time within the fixture duration")
    if content_pattern not in ("tiles", "scroll", "scroll16", "noise", "framing", "scenes", "cuts"):
        raise ValueError("Unknown synthetic content pattern")
    if content_pattern != "tiles" and (backend != "pipewire" or memory != "dmabuf"):
        raise ValueError("Detailed content patterns require the synthetic GPU PipeWire producer")
    if not isinstance(settle_after, int) or not 0 <= settle_after <= 15:
        raise ValueError("Choose a bounded content settling time")
    if settle_after and (backend != "pipewire" or memory != "dmabuf" or settle_after >= seconds):
        raise ValueError("Content settling requires a longer synthetic GPU PipeWire test")
    if memory == "dmabuf" and (backend != "pipewire" or not render_node or capture_mode == "off"):
        raise ValueError("DMA-BUF requires PipeWire, an explicit render node and capture counters")
    if aligned_output_control and memory != "dmabuf":
        raise ValueError("The padded-output diagnostic requires the DMA-BUF fixture")
    if (encoder_guard or capture_scale or capture_sync) and stall_ms and stall_target == "capture":
        raise ValueError("The custom capture-stall helper does not include encoder/scaler hooks")
    if fallback_h264 and (encoder != "av1-vaapi" or not encoder_guard):
        raise ValueError("AV1 fallback testing requires AV1 and the reviewed encoder guard")
    if record_packets and encoder not in ("av1-vaapi", "h264-vaapi", "h265-vaapi"):
        raise ValueError("Synthetic packet recording requires AV1, H.264 or HEVC")
    if encode_resolution and (
        encode_resolution not in RESOLUTIONS or aligned_output_control or quality_mode == "source"
    ):
        raise ValueError(
            "Choose a supported fixed target size without Source or padded-output mode"
        )
    if decoder == "electron" and not render_node:
        raise ValueError("Electron GPU decoding requires an explicit render node")
    if (
        render_node
        and encoder not in ("h264-vaapi", "h265-vaapi", "av1-vaapi")
        and memory != "dmabuf"
        and decoder != "electron"
    ):
        raise ValueError("GPU access requires a hardware encoder, decoder or DMA-BUF fixture")
    if capture_mode != "off" and backend != "pipewire":
        raise ValueError("Capture counters require the native PipeWire fixture")
    if capture_scale and backend != "pipewire":
        raise ValueError("The GPU capture scaler requires the native PipeWire fixture")
    if (capture_sync or capture_picker) and backend != "pipewire":
        raise ValueError("Capture synchronization requires the native PipeWire fixture")
    if capture_delivery not in ("normal", "probe", "fast") or (
        capture_delivery != "normal" and not capture_picker
    ):
        raise ValueError("Capture delivery experiments require the external native picker")
    if scenario != "steady" and seconds < 9:
        raise ValueError("Transition scenarios need at least nine seconds")
    if pressure not in ("none", "cpu", "gpu", "both"):
        raise ValueError("Unknown pressure mode")
    if (pressure != "none") != (scenario == "pressure"):
        raise ValueError("Pressure requires the pressure scenario and an explicit pressure mode")
    if pressure != "none" and (backend != "pipewire" or seconds < 12 or seconds > 30):
        raise ValueError("Pressure needs private PipeWire and a 12–30 second fixture")
    if pressure in ("gpu", "both") and not render_node:
        raise ValueError("GPU pressure requires an explicit render node")
    if backend == "pipewire" and motion != "moving":
        raise ValueError("The synthetic PipeWire producer currently requires moving content")
    if stall_ms and (backend != "pipewire" or stall_ms not in (100, 200)):
        raise ValueError("A bounded consumer stall requires the private PipeWire fixture")
    if stall_ms and stall_target == "capture" and capture_mode == "off":
        raise ValueError("The callback pause requires a capture adapter mode")
    if output.exists() or output.is_relative_to(runtime.parent):
        raise ValueError("Choose a fresh output outside the runtime distribution")
    for program in ("bwrap", "xvfb-run", "Xvfb", "nice"):
        if not shutil.which(program):
            raise RuntimeError(f"Missing fixture dependency: {program}")
    if not runtime.is_file() or not voice.is_file():
        raise ValueError("Pass an existing Electron executable and discord_voice.node")
    if openh264 and ((encoder != "h264-vaapi" and not fallback_h264) or not openh264.is_file()):
        raise ValueError("OpenH264 requires an existing trusted decoder library and H.264 mode")
    output.mkdir(parents=True)
    subprocess.run(
        ["cp", "-a", "--reflink=auto", str(runtime.parent), str(output / "runtime")], check=True
    )
    # Replace resources only in the copied runtime. Never follow its symlink.
    resources = output / "runtime/resources"
    if resources.is_symlink():
        resources.unlink()
    elif resources.exists():
        shutil.rmtree(resources)
    fixture = resources / "app"
    shutil.copytree(ROOT / "tests/screenshare", fixture)
    if transport_adapter:
        shutil.copy2(ROOT / "integration/video/transport.cjs", fixture / "transport-adapter.cjs")
    (output / "voice").mkdir()
    shutil.copy2(voice, output / "voice/discord_voice.node")
    if openh264:
        shutil.copy2(openh264, output / "voice/libopenh264.so")
    if capture_mode != "off" or encoder_guard or capture_scale or capture_sync or capture_picker:
        shutil.copy2(
            ROOT / "integration/video/dpe-native-identity.cjs", fixture / "dpe-native-identity.cjs"
        )
        build_capture_helper(
            output / "helper-build",
            hevc=capture_transport == "direct-gpu" or encoder in ("h265-vaapi", "av1-vaapi"),
        )
        shutil.copy2(
            output / "helper-build/libdpe-camera-probe.so", output / "libdpe-camera-probe.so"
        )
        write_capture_scope(output / "voice/discord_voice.node", output / "voice")
        if capture_scale:
            write_capture_scale_scope(output / "voice/discord_voice.node", output / "voice")
            shutil.copy2(
                ROOT / "integration/video/capture-scale.cjs", fixture / "capture-scale-adapter.cjs"
            )
        if capture_sync:
            write_capture_sync_scope(output / "voice/discord_voice.node", output / "voice")
            shutil.copy2(
                ROOT / "integration/video/capture-sync.cjs", fixture / "capture-sync-adapter.cjs"
            )
        if capture_picker:
            write_capture_picker_scope(output / "voice/discord_voice.node", output / "voice")
            shutil.copy2(
                ROOT / "integration/video/capture-picker.cjs",
                fixture / "capture-picker-adapter.cjs",
            )
        write_capture_upstream_scope(output / "voice/discord_voice.node", output / "voice")
        shutil.copy2(ROOT / "integration/video/capture.cjs", fixture / "capture-adapter.cjs")
        shutil.copy2(
            ROOT / "integration/video/capture-upstream.cjs", fixture / "dpe-capture-upstream.cjs"
        )
        if encoder_guard:
            write_encoder_scope(output / "voice/discord_voice.node", output / "voice")
            shutil.copy2(ROOT / "integration/video/encoder.cjs", fixture / "encoder-adapter.cjs")
        if stall_ms and stall_target == "capture":
            build_capture_delay_fixture(output / "libdpe-camera-probe.so")
    for name in ("libmediapipe.so", "gpu_encoder_helper"):
        dependency = voice.with_name(name)
        if dependency.exists():
            shutil.copy2(dependency, output / "voice" / name)
    for directory in ("home", "run"):
        (output / directory).mkdir(mode=0o700)
    width, height = map(int, resolution.split("x"))
    encode_width, encode_height = map(int, (encode_resolution or resolution).split("x"))
    config = dict(
        source=source,
        motion=motion,
        fps=fps,
        source_fps=source_fps or fps,
        seconds=seconds,
        drop_video=drop_video,
        encoder=encoder,
        width=width,
        height=height,
        encode_width=(width + 63) // 64 * 64 if aligned_output_control else encode_width,
        encode_height=(height + 63) // 64 * 64 if aligned_output_control else encode_height,
        aligned_output_control=aligned_output_control,
        bitrate=bitrate,
        receiver_bitrate=receiver_bitrate,
        scenario=scenario,
        backend=backend,
        capture_mode=capture_mode,
        stall_ms=stall_ms,
        stall_target=stall_target,
        memory=memory,
        render_node=str(render_node.resolve()) if render_node else None,
        gpu_access=bool(render_node),
        openh264=bool(openh264),
        pressure=pressure,
        decoder=decoder,
        quality_mode=quality_mode,
        expect_resolution=expect_resolution,
        display_backend=display_backend,
        decoder_log=decoder_log,
        encoder_guard=encoder_guard,
        fallback_h264=fallback_h264,
        record_packets=record_packets,
        transport_adapter=transport_adapter,
        degradation=degradation,
        content_pattern=content_pattern,
        transport_profile=transport_profile,
        receiver_announcements=(
            receiver_reports(receiver_bitrate or bitrate, math.ceil(seconds) + 10)
            if transport_profile == "production"
            else None
        ),
        capture_scale=capture_scale,
        settle_after=settle_after,
        reconfigure_after=reconfigure_after,
        network_profile=network_profile,
        sync_timeline=sync_timeline,
        sync_delay_ms=sync_delay_ms,
        capture_sync=capture_sync,
        capture_picker=capture_picker,
        capture_delivery=capture_delivery,
        capture_transport=capture_transport,
        profile_pipeline=profile_pipeline,
        capture_priority=capture_priority,
        split_peers=split_peers,
        baseline_seconds=baseline_seconds,
        external_pipewire_node=pipewire_node,
    )
    write_json(output / "config.json", config)
    write_json(
        output / "inputs.json",
        {
            "runtime_sha256": sha256(runtime),
            "voice_sha256": sha256(voice),
            "openh264_sha256": sha256(openh264) if openh264 else None,
            "config": config,
        },
    )
    env = {
        "PATH": os.defpath,
        "LANG": "C.UTF-8",
        "HOME": str(output / "home"),
        "XDG_CONFIG_HOME": str(output / "home/config"),
        "XDG_CACHE_HOME": str(output / "home/cache"),
        "XDG_RUNTIME_DIR": str(output / "run"),
        "DPE_SCREENSHARE_OUTPUT": str(output),
        "PULSE_SERVER": f"unix:{output}/run/no-pulse",
        "PIPEWIRE_REMOTE": "no-pipewire",
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path={output}/run/no-bus",
        "DBUS_SYSTEM_BUS_ADDRESS": f"unix:path={output}/run/no-system-bus",
    }
    if profile_pipeline:
        env["DPE_CAPTURE_PROFILE"] = "1"
    env["DPE_CAPTURE_PRIORITY"] = capture_priority
    if not render_node:
        env["LIBGL_ALWAYS_SOFTWARE"] = "1"
    if capture_delivery != "normal":
        env[
            "DPE_CAPTURE_DELIVERY_PROBE"
            if capture_delivery == "probe"
            else "DPE_CAPTURE_DELIVERY_FAST"
        ] = "1"
    else:
        # Keep a true unmodified-cadence control even when custom capture's
        # production default enables fresh-frame delivery.
        env["DPE_CAPTURE_DELIVERY_FAST"] = "0"
    if pressure != "none":
        flags = subprocess.check_output(
            ["pkg-config", "--cflags", "--libs", "gbm", "egl", "glesv2"], text=True
        ).split()
        subprocess.run(
            [
                "cc",
                "-std=c11",
                "-O2",
                "-Wall",
                "-Wextra",
                "-Werror",
                str(ROOT / "tests/screenshare/pressure.c"),
                *flags,
                "-o",
                str(output / "pressure"),
            ],
            check=True,
        )
    if record_packets:
        env["DPE_SYNTHETIC_QUALITY"] = "1"
    client_command = [
        str(output / "runtime" / runtime.name),
        "--ozone-platform=" + display_backend,
        "--disable-setuid-sandbox",
    ]
    if decoder == "software":
        client_command.append("--disable-gpu")
    if decoder_log:
        client_command += [
            "--enable-logging=stderr",
            "--vmodule=av1_decoder=2,vaapi_video_decoder=2,vaapi_wrapper=2,av1_vaapi_video_decoder_delegate=2,discord_video_decoder=3",
        ]
    if backend == "pipewire":
        packages = ["libpipewire-0.3"]
        gpu_sources = []
        if memory == "dmabuf":
            packages += ["gbm", "egl", "glesv2", "libdrm"]
            gpu_sources = ["-DDPE_SOURCE_DMABUF", str(ROOT / "tests/screenshare/gpu-source.c")]
        flags = subprocess.check_output(
            ["pkg-config", "--cflags", "--libs", *packages], text=True
        ).split()
        subprocess.run(
            [
                "cc",
                "-std=gnu11",
                "-O2",
                "-Wall",
                "-Wextra",
                "-Werror",
                str(ROOT / "tests/screenshare/pipewire-source.c"),
                *gpu_sources,
                *flags,
                "-o",
                str(output / "pipewire-source"),
            ],
            check=True,
        )
        (output / "pipewire.conf").write_text(
            "context.properties = { core.daemon = true core.name = dpe-capture-test support.dbus = false }\n"
            "context.spa-libs = { support.* = support/libspa-support }\n"
            "context.modules = [\n"
            " { name = libpipewire-module-protocol-native }\n"
            " { name = libpipewire-module-metadata }\n"
            " { name = libpipewire-module-spa-node-factory }\n"
            " { name = libpipewire-module-client-node }\n"
            " { name = libpipewire-module-access args = { access.force = unrestricted } }\n"
            " { name = libpipewire-module-adapter }\n"
            " { name = libpipewire-module-link-factory }\n]\n"
            "context.objects = [ { factory = spa-node-factory args = { factory.name = support.node.driver node.name = Dummy-Driver priority.driver = 20000 } } ]\n"
        )
        client_command = [
            shutil.which("uv"),
            "run",
            "--offline",
            "--no-project",
            "--python",
            "/usr/bin/python",
            "python",
            str(fixture / "pipewire-session.py"),
            *client_command,
        ]
    command = [
        "nice",
        "-n",
        "10",
        "bwrap",
        "--unshare-all",
        "--die-with-parent",
        "--ro-bind",
        "/",
        "/",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--tmpfs",
        "/tmp",
        "--tmpfs",
        "/run",
        "--bind",
        str(output),
        str(output),
        *device_arguments,
        *display_arguments,
        *external_arguments,
        *(
            ["xvfb-run", "-a", "-s", f"-screen 0 {width}x{height}x24 -nolisten tcp"]
            if display_backend == "x11"
            else []
        ),
        *client_command,
    ]
    with (output / "console.log").open("w") as log:
        process = subprocess.Popen(
            command, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
        )
        try:
            status = process.wait(timeout=seconds + 30)
        finally:
            # Also reap Xvfb or a child left behind by an abnormal Electron exit.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
    report_path = output / "report.json"
    if not report_path.exists():
        raise RuntimeError(
            f"Fixture exited {status} without a report; inspect {output}/console.log"
        )
    report = json.loads(report_path.read_text())
    if capture_delivery != "normal":
        delivery = {}
        for line in (output / "client.log").read_text(errors="replace").splitlines():
            if line.startswith("[DPE capture delivery] "):
                entry = json.loads(line.removeprefix("[DPE capture delivery] "))
                delivery[entry["requestId"]] = entry
        report["captureDelivery"] = list(delivery.values())
    logs = [output / name for name in ("discord-webrtc_1", "discord-webrtc_0")]
    lines = list(read_logs([path for path in logs if path.exists()]))
    observations = analyze(lines)["observations"]
    report["pipeline"] = pipeline_evidence(lines)
    if capture_transport == "direct-gpu":
        report["pipeline"]["direct_gpu_import_observed"] = (report.get("captureDirect") or {}).get(
            "convertedFrames", 0
        ) > 0
    report["pipewire_streaming_observed"] = any(
        "[capture_linux] State changed: Paused -> Streaming" in line for line in lines
    )
    if sync_timeline:
        report["native_explicit_sync_observed"] = any(
            "Explicit sync: enabled (compositor provides sync timeline metadata)" in line
            for line in lines
        )
        source_log = (output / "source.log").read_text(errors="replace")
        match = re.search(
            r"sync queued=(\d+) acquired=(\d+) released=(\d+) release_waits=(\d+) delay_ms=(\d+)",
            source_log,
        )
        report["syncProducer"] = (
            dict(
                zip(
                    ("queued", "acquired", "released", "release_waits", "delay_ms"),
                    map(int, match.groups()),
                    strict=True,
                )
            )
            if match
            else None
        )
    report.setdefault("config", config)
    validate_report(report, observations)
    if pressure != "none":
        pressure_path = output / "pressure.json"
        pressure_report = json.loads(pressure_path.read_text()) if pressure_path.exists() else {}
        report["pressure"] = pressure_report
        report["validation"]["pressure_verified"] = pressure_report.get("passed") is True
        report["passed"] = report["passed"] and report["validation"]["pressure_verified"]
    report["passed"] = bool(not status and report["passed"])
    write_json(report_path, report)
    if status or not report.get("passed"):
        raise RuntimeError(f"Native viewer delivery check failed; inspect {report_path}")
    return report_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--voice", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source", choices=("screen", "window"), default="screen")
    parser.add_argument("--backend", choices=("x11", "pipewire"), default="x11")
    parser.add_argument(
        "--capture-mode", choices=("off", "observe", "latest", "paced"), default="off"
    )
    parser.add_argument(
        "--memory",
        choices=("memfd", "dmabuf"),
        default="memfd",
        help="Synthetic PipeWire buffer type; DMA-BUF requires --render-node",
    )
    parser.add_argument(
        "--stall-ms",
        type=int,
        choices=(0, 100, 200),
        default=0,
        help="Pause only the private test client, without generating host load",
    )
    parser.add_argument("--stall-target", choices=("capture", "client"), default="capture")
    parser.add_argument(
        "--sync-timeline",
        action="store_true",
        help="Require real DRM acquire/release timelines from the synthetic GPU producer",
    )
    parser.add_argument(
        "--sync-delay-ms",
        type=int,
        default=0,
        help="Delay synthetic pixels and acquire point by 0–100 ms, to test consumer synchronization",
    )
    parser.add_argument("--motion", choices=("static", "moving"), default="moving")
    parser.add_argument(
        "--content-pattern",
        choices=("tiles", "scroll", "scroll16", "noise", "framing", "scenes", "cuts"),
        default="tiles",
        help="Synthetic GPU content: simple tiles, moving detailed texture, or worst-case noise",
    )
    parser.add_argument(
        "--settle-after",
        type=int,
        choices=range(1, 16),
        default=0,
        help="Switch GPU content to a quiet image after this many seconds to check recovery",
    )
    parser.add_argument(
        "--reconfigure-after",
        type=float,
        default=0,
        help="Reapply identical transport settings after encoder metadata becomes available",
    )
    parser.add_argument("--fps", type=int, choices=(5, 15, 30, 60, 120, 240), default=30)
    parser.add_argument(
        "--source-fps",
        type=int,
        choices=(5, 15, 30, 60, 120, 240),
        help="Synthetic source update rate; independent of requested stream FPS",
    )
    parser.add_argument("--seconds", type=int, choices=range(4, 61), default=6)
    parser.add_argument(
        "--encoder", choices=("vp8", "h264-vaapi", "h265-vaapi", "av1-vaapi"), default="vp8"
    )
    parser.add_argument("--encoder-guard", action="store_true")
    parser.add_argument("--capture-scale", action="store_true")
    parser.add_argument("--capture-sync", action="store_true")
    parser.add_argument("--capture-picker", action="store_true")
    parser.add_argument(
        "--profile-pipeline", action="store_true", help="Opt-in native stage CPU/wall counters"
    )
    parser.add_argument(
        "--capture-priority",
        choices=("inherit", "balanced"),
        default="inherit",
        help="Experimental capture-thread preference within existing user limits",
    )
    parser.add_argument("--capture-delivery", choices=("normal", "probe", "fast"), default="normal")
    parser.add_argument(
        "--capture-transport", choices=("pipewire", "direct-gpu"), default="pipewire"
    )
    parser.add_argument("--pipewire-socket", type=Path, help="Explicit owned test producer remote")
    parser.add_argument("--pipewire-node", type=int, help="Owned synthetic workload's node ID")
    parser.add_argument(
        "--transport-adapter",
        action="store_true",
        help="Exercise production pacing and active/idle diagnostics around the native sender",
    )
    parser.add_argument(
        "--fallback-h264",
        action="store_true",
        help="Synthetic shared codec renegotiation after an AV1 guard rejection",
    )
    parser.add_argument(
        "--record-packets",
        action="store_true",
        help="Save bounded synthetic fixed-key H.264/AV1/HEVC packets for independent decoder checks",
    )
    parser.add_argument("--decoder", choices=("software", "electron"), default="software")
    parser.add_argument(
        "--decoder-log", action="store_true", help="Record targeted decoder diagnostics"
    )
    parser.add_argument(
        "--display-backend",
        choices=("x11", "wayland"),
        default="x11",
        help="Wayland opens only a hidden synthetic receiver window; requires private PipeWire capture",
    )
    parser.add_argument("--quality-mode", choices=("fixed", "source"), default="fixed")
    parser.add_argument(
        "--degradation",
        choices=("production", "balanced"),
        default="production",
        help="Use Discord screenshare adaptation; balanced retains the earlier fixture control",
    )
    parser.add_argument(
        "--transport-profile",
        choices=("production", "fixed"),
        default="production",
        help="Use observed Discord startup/profile defaults or the earlier fixed-budget control",
    )
    parser.add_argument("--expect-resolution", help="Require this exact decoded width x height")
    parser.add_argument(
        "--network-profile",
        choices=("none", "bottleneck"),
        default="none",
        help="Bounded sender video link: 8/2/8 Mbps, 25 ms delay, 100 ms queue; feedback unchanged",
    )
    parser.add_argument(
        "--encode-resolution",
        choices=RESOLUTIONS,
        help="Fixed output size independent of source size",
    )
    parser.add_argument(
        "--resolution",
        choices=RESOLUTIONS,
        default="640x360",
    )
    parser.add_argument(
        "--receiver-bitrate",
        type=int,
        help="Synthetic receiver allowance, bounded by the stream ceiling",
    )
    parser.add_argument(
        "--bitrate",
        type=int,
        choices=(1000000, 4000000, 6000000, 12000000, 24000000),
        default=1000000,
    )
    parser.add_argument(
        "--render-node", type=Path, help="Explicitly expose this GPU render device to the test"
    )
    parser.add_argument("--openh264", type=Path, help="Existing trusted OpenH264 decoder library")
    parser.add_argument(
        "--scenario", choices=("steady", "rate-step", "cadence-step", "pressure"), default="steady"
    )
    parser.add_argument("--pressure", choices=("none", "cpu", "gpu", "both"), default="none")
    parser.add_argument(
        "--aligned-output-control",
        action="store_true",
        help="Diagnostic only: request 64-pixel-aligned output to test the native GPU fallback",
    )
    parser.add_argument(
        "--drop-video", action="store_true", help="Negative control: discard video; expect failure"
    )
    parser.add_argument(
        "--split-peers",
        action="store_true",
        help="Separate sender and receiver renderer processes for resource accounting",
    )
    parser.add_argument(
        "--baseline-seconds",
        type=int,
        default=0,
        help="Idle peer baseline before sharing (0–10 seconds, split peers only)",
    )
    args = parser.parse_args()
    print(check(**vars(args)))


if __name__ == "__main__":
    main()
