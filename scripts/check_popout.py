"""Check Discord video popouts on a private Xvfb/niri desktop, without an account."""

import argparse
import json
import os
import select
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from common import ROOT, sha256, write_json


def check(runtime: Path, core: Path, output: Path, preferences: dict):
    runtime, core, output = runtime.resolve(), core.resolve(), output.resolve()
    if output.exists() or output.is_relative_to(runtime.parent):
        raise ValueError("Choose a fresh output outside the runtime distribution")
    output.mkdir(parents=True)
    subprocess.run(
        ["cp", "-a", "--reflink=auto", str(runtime.parent), str(output / "runtime")], check=True
    )
    resources = output / "runtime/resources"
    if resources.is_symlink():
        resources.unlink()
    elif resources.exists():
        shutil.rmtree(resources)
    entry = resources / "app"
    entry.mkdir(parents=True)
    write_json(entry / "package.json", {"name": "dpe-popout-fixture", "main": "index.cjs"})
    (entry / "index.cjs").write_text(
        f"require({json.dumps(str(ROOT / 'tests/popout/main.cjs'))});\n"
    )
    children, logs = [], []
    with tempfile.TemporaryDirectory(prefix="dpe-pip-") as temporary:
        run = Path(temporary)
        env = {
            "PATH": os.defpath,
            "LANG": "C.UTF-8",
            "HOME": str(output / "home"),
            "XDG_CONFIG_HOME": str(output / "config"),
            "XDG_CACHE_HOME": str(output / "cache"),
            "XDG_RUNTIME_DIR": str(run),
            "DBUS_SESSION_BUS_ADDRESS": f"unix:path={run}/no-bus",
            "DBUS_SYSTEM_BUS_ADDRESS": f"unix:path={run}/no-system",
            "PULSE_SERVER": f"unix:{run}/no-pulse",
            "LIBGL_ALWAYS_SOFTWARE": "1",
            "DPE_POPOUT_OUTPUT": str(output / "result"),
            "DPE_POPOUT_CORE": str(core),
            "DPE_POPOUT_PREFS": json.dumps(preferences),
        }

        def spawn(args, name, pipe=False):
            log = (output / f"{name}.log").open("w")
            logs.append(log)
            child = subprocess.Popen(
                args,
                env=env,
                stdout=subprocess.PIPE if pipe else log,
                stderr=log,
                start_new_session=True,
            )
            children.append(child)
            return child

        try:
            # Xvfb allocates a free display; never inherit or query the user's one.
            xvfb = spawn(
                ["Xvfb", "-displayfd", "1", "-screen", "0", "1600x1000x24", "-nolisten", "tcp"],
                "xvfb",
                pipe=True,
            )
            if not select.select([xvfb.stdout], [], [], 10)[0]:
                raise RuntimeError("Xvfb did not allocate a display")
            env["DISPLAY"] = ":" + xvfb.stdout.readline().decode().strip()
            niri = spawn(["niri", "-c", str(ROOT / "tests/wayland/niri.kdl")], "niri")
            deadline = time.monotonic() + 15
            while True:
                sockets = list(run.glob("niri*.sock"))
                wayland = [p for p in run.glob("wayland-*") if p.is_socket()]
                if sockets and wayland:
                    break
                if time.monotonic() >= deadline or niri.poll() is not None:
                    raise RuntimeError("Private compositor failed; inspect niri.log")
                time.sleep(0.1)
            env.pop("DISPLAY")
            env.update(
                NIRI_SOCKET=str(sockets[0]),
                WAYLAND_DISPLAY=wayland[0].name,
                XDG_SESSION_TYPE="wayland",
            )
            process = spawn(
                [
                    str(output / "runtime" / runtime.name),
                    "--ozone-platform=wayland",
                    "--disable-gpu",
                    "--disable-dev-shm-usage",
                ],
                "electron",
            )
            code = process.wait(timeout=40)
            report_path = output / "result/report.json"
            report = json.loads(report_path.read_text())
            report.update(
                executable_sha256=sha256(runtime),
                core_sha256=sha256(core),
                process_exit=code,
                preferences=preferences,
            )
            write_json(report_path, report)
            if code or not report["passed"]:
                raise RuntimeError(report.get("error", "Popout fixture failed"))
            print(f"Passed {len(report['cases'])} popout checks: {report_path}")
        finally:
            for child in reversed(children):
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGTERM)
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid, signal.SIGKILL)
                        child.wait()
            for log in logs:
                log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runtime", type=Path)
    parser.add_argument("--core", type=Path, required=True, help="Extracted desktop-core bundle.js")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--no-minimum-size", action="store_true")
    args = parser.parse_args()
    check(args.runtime, args.core, args.output, {"minimumWindowSize": not args.no_minimum_size})


if __name__ == "__main__":
    main()
