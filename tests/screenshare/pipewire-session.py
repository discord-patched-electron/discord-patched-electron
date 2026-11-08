"""Run a bounded private PipeWire/portal fixture inside the existing sandbox."""

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from pressure_session import Pressure

root = Path(os.environ["DPE_SCREENSHARE_OUTPUT"])
assert {name for _, name in socket.if_nameindex()} == {"lo"}
config = json.loads((root / "config.json").read_text())
run = Path(tempfile.mkdtemp(prefix="dpe-capture-"))
environment = {
    **os.environ,
    "PIPEWIRE_REMOTE": "dpe-capture-test",
    "WAYLAND_DISPLAY": (
        "/run/dpe-screen-test-wayland"
        if config.get("display_backend") == "wayland"
        else "dpe-synthetic-no-compositor"
    ),
    "XDG_SESSION_TYPE": "wayland",
    "XDG_CURRENT_DESKTOP": "DPEFixture",
    "XDG_RUNTIME_DIR": str(run),
    "DBUS_SESSION_BUS_ADDRESS": "unix:path=" + str(run / "bus"),
    "DBUS_SYSTEM_BUS_ADDRESS": "unix:path=" + str(run / "no-system-bus"),
}
children, logs = [], []


def start(command, name, *, own_group=False):
    log = (root / (name + ".log")).open("w")
    logs.append(log)
    child = subprocess.Popen(
        command, env=environment, stdout=log, stderr=subprocess.STDOUT, start_new_session=own_group
    )
    children.append(child)
    return child


def ready(predicate):
    deadline = time.monotonic() + 10
    while not predicate():
        if time.monotonic() >= deadline or any(child.poll() is not None for child in children):
            raise RuntimeError("Private capture service failed; inspect fixture logs")
        time.sleep(0.05)


status = 1
pressure = Pressure(root, config, environment) if config.get("pressure", "none") != "none" else None
try:
    start(
        [
            "dbus-daemon",
            "--session",
            "--nofork",
            "--address=" + environment["DBUS_SESSION_BUS_ADDRESS"],
        ],
        "bus",
    )
    ready(lambda: (run / "bus").exists())
    if config.get("external_pipewire_node"):
        (run / "dpe-capture-test").symlink_to("/run/dpe-external-pipewire")
        (root / "source.log").write_text(str(config["external_pipewire_node"]) + "\n")
    else:
        start(["pipewire", "-c", str(root / "pipewire.conf")], "pipewire")
        ready(lambda: (run / "dpe-capture-test").exists())
        start(["wireplumber", "--profile=policy"], "policy")
        start(
            [
                str(root / "pipewire-source"),
                str(config["width"]),
                str(config["height"]),
                str(config["source_fps"]),
                *(["--dmabuf", config["render_node"]] if config.get("memory") == "dmabuf" else []),
                *(
                    ["--sync-timeline", str(config.get("sync_delay_ms", 0))]
                    if config.get("sync_timeline")
                    else []
                ),
                "--pattern",
                config.get("content_pattern", "tiles"),
                *(
                    ["--settle-after", str(config["settle_after"])]
                    if config.get("settle_after")
                    else []
                ),
            ],
            "source",
        )
    ready(lambda: any(line.isdecimal() for line in (root / "source.log").read_text().splitlines()))
    start([sys.executable, str(Path(__file__).with_name("portal.py"))], "portal")
    ready(lambda: (root / "portal-ready").exists())
    client = start(sys.argv[1:], "client", own_group=True)
    if config.get("stall_ms") and config.get("stall_target") == "client":
        ready(lambda: (root / "capture-started").exists())
        time.sleep(0.6)
        started = time.monotonic_ns()
        os.killpg(client.pid, signal.SIGSTOP)
        try:
            time.sleep(config["stall_ms"] / 1000)
        finally:
            os.killpg(client.pid, signal.SIGCONT)
        (root / "stall.json").write_text(
            json.dumps(
                {
                    "scope": "only the private fixture's Electron process group",
                    "requested_ms": config["stall_ms"],
                    "elapsed_ms": (time.monotonic_ns() - started) / 1e6,
                }
            )
        )
    deadline = time.monotonic() + config["seconds"] + 20
    while client.poll() is None:
        if time.monotonic() > deadline:
            raise RuntimeError("Private client exceeded its deadline")
        if pressure:
            pressure.poll()
        time.sleep(0.02)
    status = client.returncode
finally:
    if pressure:
        pressure.close()
    if "client" in globals():
        try:
            os.killpg(client.pid, signal.SIGCONT)
            os.killpg(client.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    for child in reversed(children):
        if child.poll() is None:
            child.send_signal(signal.SIGTERM)
    for child in reversed(children):
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
    if "client" in globals():
        try:
            os.killpg(client.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    for log in logs:
        log.close()
    shutil.rmtree(run)
raise SystemExit(status)
