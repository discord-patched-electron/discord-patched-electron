"""Check native Wayland selection, GPU rendering and synthetic video playback."""

import argparse
import os
import shutil
import subprocess
from pathlib import Path

from common import ROOT, read_json, run, sha256, write_json


def check(
    runtime: Path,
    output: Path,
    extra: list[str],
    openasar_source: Path | None = None,
    preset: str = "perf",
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
        "-c:v",
        "libx264",
        "-threads",
        "2",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        fixture / "clip.mp4",
    )
    run(ROOT / "node_modules/.bin/asar", "pack", fixture, output / "runtime/resources/app.asar")
    env = {
        **os.environ,
        "DPE_GRAPHICS_OUTPUT": str(output),
        "DPE_GRAPHICS_PRESET": preset,
        "WAYLAND_DEBUG": "client",
    }
    env.pop("ELECTRON_RUN_AS_NODE", None)
    with (output / "console.log").open("w") as log:
        process = subprocess.run(
            [str(output / "runtime" / runtime.name), "--disable-setuid-sandbox", *extra],
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
    parser.add_argument(
        "--openasar-source",
        type=Path,
        help="Apply a pinned bootstrap/src/cmdSwitches.js through the current compatibility adapter",
    )
    parser.add_argument("--preset", choices=("perf", "battery", "balanced"), default="perf")
    args = parser.parse_args()
    if args.preset != "perf" and not args.openasar_source:
        parser.error("--preset requires --openasar-source")
    if not check(args.runtime, args.output, args.flag, args.openasar_source, args.preset)["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
