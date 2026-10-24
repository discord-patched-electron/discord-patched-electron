"""Measure real webcam startup in an isolated local test app; never save images.

Requires an explicitly selected physical camera and a Discord-compatible runtime.
Does not start a call, load a profile, record a microphone or display camera pixels.
"""

import argparse
import os
from pathlib import Path
import re
import resource
import subprocess

from build_camera_support import build
from camera_scope import write_scope
from common import ROOT, read_json, sha256, write_json
from fetch_reference import validate_capacity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runtime",
        type=Path,
        required=True,
        help="Path to a bundled Discord or compiled Electron executable",
    )
    parser.add_argument(
        "--voice", type=Path, required=True, help="Path to discord_voice.node"
    )
    parser.add_argument("--device", required=True, help="Exact webcam name from V4L2")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=2)
    parser.add_argument("--compare-async", action="store_true", help="Compare sync/async initialization, with the fast probe enabled in both")
    parser.add_argument("--provenance", type=Path, help="Verified compiler artifact provenance for a custom runtime")
    args = parser.parse_args()
    if not 1 <= args.samples <= 5:
        parser.error("--samples must be between 1 and 5")
    provenance = read_json(args.provenance) if args.provenance else None
    if provenance is not None:
        capacity = provenance["capacity"]
        validate_capacity(capacity, capacity["source"]["target"] != "reference")
    output = args.output.resolve()
    if output.exists():
        parser.error("Use a new output directory")
    output.mkdir(parents=True)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    # Copy the runtime; replacing its app archive must never touch the installed app.
    subprocess.run(
        [
            "cp",
            "-a",
            "--reflink=auto",
            str(args.runtime.resolve().parent),
            str(output / "runtime"),
        ],
        check=True,
    )
    subprocess.run(
        [
            "cp",
            "-a",
            "--reflink=auto",
            str(args.voice.resolve().parent),
            str(output / "voice"),
        ],
        check=True,
    )
    subprocess.run(
        [
            str(ROOT / "node_modules/.bin/asar"),
            "pack",
            str(ROOT / "tests/camera"),
            str(output / "runtime/resources/app.asar"),
        ],
        check=True,
    )
    helper = build(output / "support")
    scope = write_scope(output / "voice/discord_voice.node", output)
    # Generated tone, never microphone input. Probe loopback packet delivery;
    # the report explicitly distinguishes startup silence from sustained audio.
    import math
    import struct
    import wave
    with wave.open(str(output / 'synthetic-audio.wav'), 'wb') as wav:
        wav.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
        wav.writeframes(b''.join(struct.pack('<h', int(8000 * math.sin(2 * math.pi * 440 * i / 48000))) for i in range(48000 * 15)))
    results = []
    for sample in range(args.samples):
        for enabled in (False, True):
            run = output / f"{'patched' if enabled else 'upstream'}-{sample + 1}"
            run.mkdir()
            env = {
                **os.environ,
                "DPE_CAMERA_OUTPUT": str(run),
                "DPE_CAMERA_DEVICE": args.device,
                "DPE_CAMERA_FAKE_AUDIO": str(output / 'synthetic-audio.wav'),
                "DPE_CAMERA_VOICE_MODULE": str(output / "voice/discord_voice.node"),
                "DPE_CAMERA_PROBE_START": str(scope["start"]),
                "DPE_CAMERA_PROBE_END": str(scope["end"]),
                "DPE_CAMERA_FAST_PROBE": "1" if enabled else "0",
                "DPE_CAMERA_ASYNC_INIT": "1" if args.compare_async and enabled else "0",
                "LD_PRELOAD": str(output / "support/libdpe-camera-probe.so"),
            }
            env.pop("ELECTRON_RUN_AS_NODE", None)
            if args.compare_async:
                env["DPE_CAMERA_FAST_PROBE"] = "1"
            with (run / "console.log").open("w") as log:
                subprocess.run(
                    [
                        str(output / "runtime" / args.runtime.name),
                        "--ozone-platform=wayland",
                        "--disable-setuid-sandbox",
                    ],
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=35,
                    check=True,
                )
            result = read_json(run / "result.json")
            if provenance is not None and result["versions"]["electron"] != capacity["compiled_version"]:
                raise ValueError("Loaded Electron version differs from the compiler report")
            result.update(patched=enabled, sample=sample + 1)
            result["async_video_input_device_init"] = env["DPE_CAMERA_ASYNC_INIT"] == "1"
            # Retain measurements, not raw logs (which include device paths).
            log = (run / "discord-webrtc_0").read_text(errors="replace")
            times = re.findall(
                r"^\[([^\]]+)\].*CreateCapabilityMap (?:called for device|\d+)",
                log,
                re.M,
            )
            if len(times) >= 2:
                from datetime import datetime

                result["capability_probe_ms"] = (
                    datetime.fromisoformat(times[1]) - datetime.fromisoformat(times[0])
                ).total_seconds() * 1000
            results.append(result)
            write_json(
                output / "camera-report.json",
                {
                    "schema": 1,
                    "camera": args.device,
                    "scope": scope,
                    "helper": helper,
                    "runtime_sha256": sha256(args.runtime),
                    "physical_camera_verified": True,
                    "images_saved": False,
                    "microphone_used": False,
                    "real_discord_call_verified": False,
                    "custom_runtime_verified": provenance is not None,
                    "compiler_run": provenance.get("compiler_run") if provenance else None,
                    "runs": results,
                },
            )
            print(
                f"{'patched' if enabled else 'upstream'}: {result['first_frame_ms']:.1f} ms to first frame",
                flush=True,
            )


if __name__ == "__main__":
    main()
