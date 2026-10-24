"""Exercise main-process frame capture and GC with synthetic local pages only."""
import argparse
import os
from pathlib import Path
import subprocess

from common import ROOT, read_json, run, sha256, write_json
from fetch_reference import validate_capacity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runtime", type=Path)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    capacity = read_json(args.provenance)["capacity"]
    validate_capacity(capacity, capacity["source"]["target"] != "reference")
    runtime, output = args.runtime.resolve(), args.output.resolve()
    if output.exists() or output.is_relative_to(runtime.parent):
        raise ValueError("Choose a fresh output outside the input distribution")
    output.mkdir(parents=True)
    run("cp", "-a", "--reflink=auto", runtime.parent, output / "runtime")
    run(ROOT / "node_modules/.bin/asar", "pack", ROOT / "tests/frame-capture",
        output / "runtime/resources/app.asar")
    env = {**os.environ, "DPE_FRAME_CAPTURE_OUTPUT": str(output)}
    env.pop("ELECTRON_RUN_AS_NODE", None)
    with (output / "console.log").open("w") as log:
        process = subprocess.run([str(output / "runtime" / runtime.name), "--disable-setuid-sandbox"],
            env=env, stdout=log, stderr=subprocess.STDOUT, timeout=40)
    report = read_json(output / "frame-capture.json")
    report.update(executable_sha256=sha256(runtime), process_exit=process.returncode,
        compiler_run=read_json(args.provenance)["compiler_run"])
    report["passed"] = (report.get("passed") is True and process.returncode == 0
        and report["versions"]["electron"] == capacity["compiled_version"])
    write_json(output / "frame-capture.json", report)
    print({key: report.get(key) for key in ("passed", "versions", "cases", "error")})
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
