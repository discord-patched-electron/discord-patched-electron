"""Exercise synchronous Discord IPC against Electron's async safeStorage API.

Only synthetic test data, in a disposable profile; no user keyring is accessed.
"""

import argparse
import os
import subprocess
from pathlib import Path

from common import ROOT, read_json, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error("Choose a fresh output directory")
    output.mkdir(parents=True)
    subprocess.run(
        ["cp", "-a", "--reflink=auto", str(args.runtime.resolve().parent), str(output / "runtime")],
        check=True,
    )
    subprocess.run(
        [
            str(ROOT / "node_modules/.bin/asar"),
            "pack",
            str(ROOT / "tests/safe-storage"),
            str(output / "runtime/resources/app.asar"),
        ],
        check=True,
    )
    env = {
        **os.environ,
        "DPE_STORAGE_OUTPUT": str(output),
        "DPE_STORAGE_ADAPTER": str(ROOT / "integration/desktop/safe-storage.cjs"),
    }
    env.pop("ELECTRON_RUN_AS_NODE", None)
    results = []
    for attempt in range(2):
        with (output / f"console-{attempt}.log").open("w") as log:
            subprocess.run(
                [
                    str(output / "runtime" / args.runtime.name),
                    "--disable-setuid-sandbox",
                    "--ozone-platform=" + ("wayland" if env.get("WAYLAND_DISPLAY") else "x11"),
                ],
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=25,
            )
        result = read_json(output / "storage.json")
        if result.get("passed") is not True or result.get("reopened") != bool(attempt):
            raise ValueError("Safe storage persistence check failed")
        results.append(result)
    write_json(
        output / "storage-report.json", {"passed": True, "synthetic_only": True, "runs": results}
    )
    print("safeStorage IPC, invalid ciphertext and relaunch: passed")


if __name__ == "__main__":
    main()
