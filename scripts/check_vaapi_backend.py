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
    binary = output / "backend-check"
    subprocess.run(
        [
            "cc",
            "-std=c11",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-I" + str(ROOT / "integration/video"),
            str(ROOT / "tests/screenshare/vaapi-backend-check.c"),
            "-ldl",
            "-o",
            str(binary),
        ],
        check=True,
    )
    files = [output / "generated.265", output / "generated.obu"]
    recovery = [output / "recovery.265", output / "recovery.obu"]
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
        expected = "1" if file in recovery else "9"
        if stream.get("nb_read_frames") != expected or probe.stderr:
            raise ValueError("Lifecycle or fresh-decoder recovery output did not decode cleanly")
        decoded.append(stream)
    report = {
        "passed": True,
        "backend_sha256": sha256(backend),
        "render_node": str(node),
        "decoded": decoded,
        "scope": "Generated CPU pixels, encode, suspend, resize, resume, rejected-update recovery; no network",
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
