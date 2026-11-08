"""Exercise codec suspend/resize/recovery with generated pixels on an explicit GPU.

Requires the independently built backend, a C compiler and FFmpeg tools. No
account, display, camera, game or desktop source is opened.
"""

import argparse
import json
import subprocess
from pathlib import Path

from common import ROOT, sha256, write_json


def check(backend, node, output):
    if output.exists():
        raise ValueError("Choose a new validation directory")
    output.mkdir(parents=True)
    symbols = {
        line.split()[-1]
        for line in subprocess.check_output(
            ["nm", "-D", "--defined-only", str(backend)], text=True
        ).splitlines()
        if line.split()
    }
    expected_symbols = {
        "dpe_hevc_api",
        "dpe_encoder_hardware_scope",
        "dpe_encoder_import_stats",
        "dpe_encoder_profile_stage",
    }
    if symbols != expected_symbols:
        raise ValueError("Private encoder exported an unexpected ABI or codec symbol")
    binary = output / "backend-check"
    subprocess.run(
        [
            "cc",
            "-std=c11",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-pthread",
            "-I" + str(ROOT / "integration/video"),
            str(ROOT / "tests/screenshare/vaapi-backend-check.c"),
            "-ldl",
            "-o",
            str(binary),
        ],
        check=True,
    )
    files = [output / "generated.265", output / "generated.ivf"]
    recovery = [output / "recovery.265", output / "recovery.ivf"]
    with (output / "lifecycle.log").open("w") as log:
        subprocess.run(
            [str(binary), str(backend.resolve()), str(node), *map(str, [*files, *recovery])],
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=30,
        )
    decoded = []
    for file in [*files, *recovery]:
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-count_frames",
                "-show_entries",
                "stream=codec_name,nb_read_frames",
                "-of",
                "json",
                str(file),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        )
        stream = json.loads(probe.stdout)["streams"][0]
        expected = "1" if file in recovery else "21"
        # Older dav1d versions log unknown proposed metadata even at error level.
        # Permit only this diagnostic; decoding errors must still fail the test.
        diagnostics = [line.strip() for line in probe.stderr.splitlines() if line.strip()]
        metadata_only = file.suffix == ".ivf" and all(
            "Unknown Metadata OBU type 32" in line
            or (index > 0 and line.startswith("Last message repeated "))
            for index, line in enumerate(diagnostics)
        )
        if stream.get("nb_read_frames") != expected or (diagnostics and not metadata_only):
            raise ValueError("Lifecycle or fresh-decoder recovery output did not decode cleanly")
        decoded.append(stream)
    report = {
        "passed": True,
        "backend_sha256": sha256(backend),
        "render_node": str(node),
        "exported_symbols": sorted(symbols),
        "decoded": decoded,
        "scope": "Generated pixels, callback reentry/concurrent destroy, exact lease release, suspend/resize/resume and rejected-update recovery; no network",
    }
    write_json(output / "report.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", type=Path, required=True)
    parser.add_argument("--render-node", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(check(args.backend, args.render_node, args.output), indent=2))
