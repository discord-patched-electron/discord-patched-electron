"""Bounded GSR KMS/portal resource comparison; encoded output is discarded.

This is an exploratory capture-to-encoder measurement, not a Discord latency or
viewer-quality test. Only the owned synthetic window is selected. The portal
case uses a private activation-free bus and the existing test portal fixture.
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from xml.sax.saxutils import escape

from benchmark_capture_methods import Run
from common import ROOT, sha256, write_json

sys.path.insert(0, str(ROOT / "tests/screenshare"))
from pressure_session import Pressure


def recorder_command(args):
    return [
        str(args.recorder),
        "-w",
        args.monitor if args.method == "kms" else "portal",
        "-f",
        "60",
        "-s",
        "1920x1080",
        "-k",
        "h264",
        "-bm",
        "cbr",
        "-q",
        "6000",
        "-fm",
        "vfr",
        "-cr",
        "limited",
        "-cursor",
        "no",
        "-keyint",
        "3",
        "-encoder",
        "gpu",
        "-fallback-cpu-encoding",
        "no",
        "-restore-portal-session",
        "no",
        "-write-first-frame-ts",
        "no",
        "-c",
        "matroska",
        "-o",
        "/dev/null",
        "-v",
        "yes",
    ]


def bus_config(path):
    # Deliberately no service directories or includes: if the owned fixture
    # fails, this bus cannot activate a real screen-sharing dialog.
    return f"""<busconfig>
<type>session</type><listen>unix:path={escape(str(path))}</listen><auth>EXTERNAL</auth>
<policy context="default"><allow own="*"/><allow send_destination="*"/>
<allow receive_sender="*"/></policy>
</busconfig>
"""


class GsrRun(Run):
    def __init__(self, args):
        super().__init__(args)
        self.pressure = None

    def workload(self):
        game = self.start(
            [self.args.workload, "90", self.args.source_fps, "1920", "1080", "tiles"],
            "workload",
            dict(self.host, SDL_VIDEODRIVER="wayland"),
        )
        until = time.monotonic() + 6
        window = None
        while time.monotonic() < until:
            windows = json.loads(
                subprocess.check_output(
                    ["niri", "msg", "--json", "windows"], env=self.host, text=True, timeout=3
                )
            )
            window = next(
                (
                    w
                    for w in windows
                    if w.get("pid") == game.pid
                    and w.get("app_id") == "org.dpe.CapturePipelineBenchmark"
                ),
                None,
            )
            if window:
                break
            if game.poll() is not None:
                raise RuntimeError("Owned workload ended before creating its window")
            time.sleep(0.03)
        if not window:
            raise RuntimeError("Owned workload window missing")
        for command in (
            ["move-window-to-monitor", "--id", str(window["id"]), self.args.monitor],
            ["fullscreen-window", "--id", str(window["id"])],
        ):
            subprocess.run(
                ["niri", "msg", "action", *command],
                env=self.host,
                check=True,
                stdout=subprocess.DEVNULL,
                timeout=3,
            )
        time.sleep(0.8)

    def private_portal(self, fixture):
        session = self.start(
            [
                "/usr/bin/python3",
                ROOT / "tests/screenshare/compositor-session.py",
                self.args.monitor,
            ],
            "compositor-session",
            self.host,
            pipe=True,
        )
        ready = self.read(session)
        if ready.get("event") != "ready":
            raise RuntimeError("Owned compositor capture did not start")
        write_json(fixture / "config.json", {"source": "screen", "width": 1920, "height": 1080})
        (fixture / "source.log").write_text(str(ready["nodeId"]) + "\n")
        (self.run / "dpe-capture-test").symlink_to(
            Path(self.host["XDG_RUNTIME_DIR"]) / "pipewire-0"
        )
        config = self.run / "bus.conf"
        config.write_text(bus_config(self.run / "bus"))
        self.start(
            ["dbus-daemon", "--nofork", "--nopidfile", "--config-file", config], "dbus", self.host
        )
        self.wait(lambda: (self.run / "bus").is_socket())
        address = f"unix:path={self.run / 'bus'}"
        environment = dict(
            self.environment, DBUS_SESSION_BUS_ADDRESS=address, DPE_SCREENSHARE_OUTPUT=str(fixture)
        )
        self.start(
            [
                "bwrap",
                "--die-with-parent",
                "--unshare-user",
                "--unshare-net",
                "--ro-bind",
                "/",
                "/",
                "--bind",
                self.run,
                self.run,
                "--bind",
                self.args.output,
                self.args.output,
                "--",
                "/usr/bin/python3",
                ROOT / "tests/screenshare/portal.py",
            ],
            "private-portal",
            environment,
        )
        self.wait(lambda: (fixture / "portal-ready").is_file())
        return address

    def execute(self):
        args = self.args
        fixture = args.output / "native"
        fixture.mkdir()
        if args.pressure != "none":
            flags = subprocess.check_output(
                ["pkg-config", "--cflags", "--libs", "egl", "glesv2", "gbm"], text=True
            ).split()
            subprocess.run(
                [
                    "cc",
                    "-O2",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    str(ROOT / "tests/screenshare/pressure.c"),
                    *flags,
                    "-o",
                    str(fixture / "pressure"),
                ],
                check=True,
            )
            self.pressure = Pressure(
                fixture,
                {
                    "pressure": args.pressure,
                    "backend": "pipewire",
                    "seconds": args.seconds,
                    "render_node": str(args.render_node),
                },
                self.host,
            )
        self.workload()
        environment = dict(self.host, XDG_CONFIG_HOME=str(self.run / "config"))
        if args.method == "portal":
            environment["DBUS_SESSION_BUS_ADDRESS"] = self.private_portal(fixture)
        recorder = self.start(recorder_command(args), "recorder", environment)
        # GSR's update-FPS log confirms its capture loop started. It is NOT a
        # unique encoded-frame counter, so no delivered-FPS claim derives from it.
        self.wait(
            lambda: "update fps:" in (args.output / "recorder.log").read_text(errors="replace"), 15
        )
        (fixture / "capture-started").touch()
        self.thread = threading.Thread(target=self.monitor, daemon=True)
        self.thread.start()
        start = time.monotonic()
        while (elapsed := time.monotonic() - start) < args.seconds:
            if recorder.poll() is not None:
                raise RuntimeError(f"GSR ended during measurement ({recorder.returncode})")
            phase = (
                "initial"
                if elapsed < args.seconds / 3
                else "constrained"
                if elapsed < 2 * args.seconds / 3
                else "recovery"
            )
            (fixture / "pressure-phase").write_text(phase if self.pressure else "steady")
            if self.pressure:
                self.pressure.poll()
            time.sleep(0.1)
        recorder.send_signal(signal.SIGINT)
        status = recorder.wait(timeout=8)
        if status:
            raise RuntimeError(f"GSR did not stop cleanly ({status})")

    def close(self):
        if self.pressure:
            self.pressure.close()
        super().close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--method", choices=("kms", "portal"), required=True)
    parser.add_argument("--recorder", type=Path, default=Path("/usr/bin/gpu-screen-recorder"))
    for name in ("workload", "render-node", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--monitor", required=True)
    parser.add_argument("--seconds", type=int, choices=range(12, 31), default=20)
    parser.add_argument(
        "--source-fps",
        choices=("60", "120", "240", "600", "2000", "4000", "uncapped"),
        default="600",
    )
    parser.add_argument("--pressure", choices=("none", "cpu", "gpu", "both"), default="none")
    parser.add_argument("--run", action="store_true")
    parser.set_defaults(game_handoff="copy")
    args = parser.parse_args()
    for name in ("recorder", "workload", "render_node", "output"):
        setattr(args, name, getattr(args, name).resolve())
    if args.output.exists() or not args.recorder.is_file() or not args.workload.is_file():
        parser.error("Pass existing recorder/workload binaries and a fresh output")
    plan = {
        key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()
    }
    plan["command"] = recorder_command(args)
    plan["inputs"] = {str(file): sha256(file) for file in (args.recorder, args.workload)}
    plan["scope"] = (
        "GSR capture-to-encoder resources; discarded output; no Discord, decoder or latency measurement"
    )
    if not args.run:
        print(json.dumps(plan, indent=2))
        return
    if not os.environ.get("NIRI_SOCKET") or not os.environ.get("WAYLAND_DISPLAY"):
        parser.error("The owned-window compositor fixture requires the active niri Wayland session")
    if args.method == "kms":
        helper = args.recorder.parent / "gsr-kms-server"
        capabilities = subprocess.check_output(["getcap", str(helper)], text=True).strip()
        if capabilities != f"{helper} cap_sys_admin=ep":
            parser.error(
                "KMS benchmark requires the existing companion helper with cap_sys_admin=ep; no authorization dialog is launched"
            )
        plan["inputs"][str(helper)] = sha256(helper)
    args.output.mkdir(parents=True)
    write_json(args.output / "plan.json", plan)
    run = GsrRun(args)

    def interrupted(number, _frame):
        raise RuntimeError(f"Benchmark interrupted by signal {number}")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGALRM, interrupted)
    signal.alarm(120)
    status = "failed"
    try:
        run.execute()
        status = "passed"
    finally:
        signal.alarm(0)
        run.close()
        if (
            run.pressure
            and not json.loads((args.output / "native/pressure.json").read_text())["passed"]
        ):
            status = "failed"
        write_json(args.output / "result.json", {"status": status, "scope": plan["scope"]})
    print(args.output)
    if status != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
