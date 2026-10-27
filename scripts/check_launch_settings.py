"""Test the real launch settings UI and IPC without loading Discord or an account."""

import argparse
import os
import shutil
import subprocess
from pathlib import Path

from common import ROOT, read_json, run, sha256, write_json


def check(runtime, output):
    runtime, output = runtime.resolve(), output.resolve()
    if output.exists() or output.is_relative_to(runtime.parent):
        raise ValueError("Choose a fresh output outside the input distribution")
    output.mkdir(parents=True)
    run("cp", "-a", "--reflink=auto", runtime.parent, output / "runtime")
    fixture = output / "fixture"
    shutil.copytree(ROOT / "tests/launch-settings", fixture)
    run(ROOT / "node_modules/.bin/asar", "pack", fixture, output / "runtime/resources/app.asar")
    (output / "profile").mkdir()
    (output / "config").mkdir()
    env = {
        **os.environ,
        "DPE_SETTINGS_OUTPUT": str(output),
        "DPE_APP_ROOT": str(ROOT),
        "DPE_PROFILE_DIR": str(output / "profile"),
        "DPE_LAUNCHER": str(output / "launch.sh"),
        "DPE_LAUNCH_ARGS": "[]",
        "DPE_CLIENT_OFFLINE": "1",
        "DPE_IGNORE_FLAGS": "0",
        "DPE_DEFAULT_FLAGS": "1",
        "XDG_CONFIG_HOME": str(output / "config"),
    }
    env.pop("ELECTRON_RUN_AS_NODE", None)
    with (output / "console.log").open("w") as log:
        process = subprocess.run(
            [str(output / "runtime" / runtime.name), "--disable-setuid-sandbox"],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=60,
        )
    report = read_json(output / "settings.json")
    report.update(executable_sha256=sha256(runtime), process_exit=process.returncode)
    report["passed"] = report.get("passed") is True and process.returncode == 0
    write_json(output / "settings.json", report)
    print(report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runtime", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not check(args.runtime, args.output)["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
