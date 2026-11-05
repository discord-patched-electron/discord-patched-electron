"""Check an owned fullscreen source through portal, game or KMS capture.

One explicit, bounded case per invocation. Use the same arguments with different
methods/bundles, alternating their order for repeats. This measures the native
encode/decode pipeline locally; it does not contact Discord or save pixels.
"""

import argparse
import json
import os
import selectors
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from check_screenshare import check
from common import ROOT, sha256, write_json

PIPEWIRE_CONFIG = """
context.properties = { core.daemon = true core.name = dpe-capture-test support.dbus = false }
context.spa-libs = { support.* = support/libspa-support }
context.modules = [
 { name = libpipewire-module-protocol-native }
 { name = libpipewire-module-metadata }
 { name = libpipewire-module-spa-node-factory }
 { name = libpipewire-module-client-node }
 { name = libpipewire-module-access args = { access.force = unrestricted } }
 { name = libpipewire-module-adapter }
 { name = libpipewire-module-link-factory }
]
context.objects = [ { factory = spa-node-factory args = {
 factory.name = support.node.driver node.name = Dummy-Driver priority.driver = 20000
} } ]
"""


def descendants(pid, proc=Path("/proc")):
    result, pending = set(), [pid]
    while pending:
        parent = pending.pop()
        for file in (proc / str(parent) / "task").glob("*/children"):
            try:
                values = file.read_text().split()
            except OSError:
                continue
            for value in values:
                child = int(value)
                if child not in result:
                    result.add(child)
                    pending.append(child)
    return result


def counters(pid, proc=Path("/proc")):
    try:
        stat = (proc / str(pid) / "stat").read_text()
        values = stat.rsplit(")", 1)[1].split()
        clients = {}
        for fd in (proc / str(pid) / "fdinfo").glob("*"):
            try:
                data = dict(
                    line.split(":", 1) for line in fd.read_text().splitlines() if ":" in line
                )
                client = data.get("drm-client-id", "").strip()
                device = data.get("drm-pdev", data.get("drm-driver", "")).strip()
                if not client or (key := f"{device}/{client}") in clients:
                    continue
                clients[key] = {
                    name: int(value.split()[0])
                    for name, value in data.items()
                    if name.startswith("drm-engine-") and value.strip().endswith(" ns")
                }
            except (OSError, ValueError):
                continue
        return {
            "cpuTicks": int(values[11]) + int(values[12]),
            "startTicks": int(values[19]),
            "name": stat.split("(", 1)[1].rsplit(")", 1)[0],
            "drmClients": clients,
        }
    except (OSError, ValueError, IndexError):
        return None


def summarize_resources(samples, start=2, end=None):
    """Aggregate observed per-process deltas; exited/new processes remain visible.

    DRM client IDs deduplicate inherited/duplicated descriptors across processes.
    Counters are measured over each identity's observed lifetime, not extrapolated.
    """
    selected = [
        s for s in samples if s["seconds"] >= start and (end is None or s["seconds"] <= end)
    ]
    if len(selected) < 2:
        return {}
    elapsed = selected[-1]["seconds"] - selected[0]["seconds"]
    if elapsed <= 0:
        return {}
    groups = {}
    for sample in selected:
        for group, processes in sample["groups"].items():
            state = groups.setdefault(group, {"cpu": {}, "gpu": {}})
            for pid, value in processes.items():
                key = f"{pid}/{value['startTicks']}"
                state["cpu"].setdefault(key, [value["cpuTicks"], value["cpuTicks"]])[1] = value[
                    "cpuTicks"
                ]
                for client, engines in value["drmClients"].items():
                    for engine, ns in engines.items():
                        state["gpu"].setdefault((client, engine), [ns, ns])[1] = ns
    result = {}
    for group, state in groups.items():
        engines = {}
        for (_, engine), (first, last) in state["gpu"].items():
            engines[engine] = engines.get(engine, 0) + max(0, last - first) / 1e9 / elapsed * 100
        result[group] = {
            "cpuOneCorePercent": sum(max(0, b - a) for a, b in state["cpu"].values())
            / os.sysconf("SC_CLK_TCK")
            / elapsed
            * 100,
            "drmEnginePercent": engines,
            "sampledSeconds": elapsed,
        }
    return result


def compositor_pid(environment):
    with socket.socket(socket.AF_UNIX) as connection:
        connection.connect(environment["NIRI_SOCKET"])
        pid, _, uid = struct.unpack(
            "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        )
        if uid != os.getuid():
            raise RuntimeError("Compositor belongs to a different user")
        return pid


def needs_fullscreen(window):
    # Window rules can already open this owned workload fullscreen. The niri
    # action is a toggle; calling it blindly reverses every lifecycle phase.
    return window.get("layout", {}).get("window_size") != [1920, 1080]


class Run:
    def __init__(self, args):
        self.args = args
        self.children, self.logs, self.events, self.samples = [], [], [], []
        self.pending_lines = {}
        self.stop = threading.Event()
        self.thread = None
        self.host = dict(os.environ)
        self.compositor = compositor_pid(self.host)
        self.directory = tempfile.TemporaryDirectory(prefix="dpe-capture-bench-")
        self.run = Path(self.directory.name)
        self.environment = dict(
            self.host,
            XDG_RUNTIME_DIR=str(self.run),
            PIPEWIRE_REMOTE="dpe-capture-test",
            DPE_GAME_CAPTURE_HANDOFF=args.game_handoff,
            WAYLAND_DISPLAY=str(Path(self.host["XDG_RUNTIME_DIR"]) / self.host["WAYLAND_DISPLAY"]),
        )

    def start(self, command, name, environment=None, pipe=False):
        log = (self.args.output / f"{name}.log").open("w")
        self.logs.append(log)
        child = subprocess.Popen(
            [str(value) for value in command],
            env=environment or self.environment,
            stdout=subprocess.PIPE if pipe else log,
            stderr=log,
            stdin=subprocess.PIPE if pipe else subprocess.DEVNULL,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        self.children.append((name, child))
        return child

    def wait(self, predicate, seconds=8):
        until = time.monotonic() + seconds
        while not predicate():
            if time.monotonic() > until:
                raise RuntimeError("Owned service readiness timed out")
            if any(proc.poll() is not None for _, proc in self.children):
                raise RuntimeError("Owned process ended early")
            time.sleep(0.03)

    def read(self, child, seconds=8):
        until = time.monotonic() + seconds
        buffered = self.pending_lines.get(child.pid, b"")
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while b"\n" not in buffered:
                remaining = until - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise RuntimeError("Producer response timed out")
                data = os.read(child.stdout.fileno(), 65536)
                if not data:
                    raise RuntimeError("Producer ended before responding")
                buffered += data
                if len(buffered) > 1024 * 1024:
                    raise RuntimeError("Producer response exceeded its bound")
        line, buffered = buffered.split(b"\n", 1)
        self.pending_lines[child.pid] = buffered
        value = json.loads(line)
        self.events.append(value)
        return value

    @staticmethod
    def command(child, value):
        child.stdin.write(json.dumps(value) + "\n")
        child.stdin.flush()

    def monitor(self):
        began = None
        while not self.stop.wait(0.5):
            if not (self.args.output / "native/capture-started").exists():
                continue
            if began is None:
                began = time.monotonic()
            groups = {name: {child.pid} | descendants(child.pid) for name, child in self.children}
            assigned = set().union(*groups.values())
            groups["native"] = descendants(os.getpid()) - assigned
            groups["compositor"] = {self.compositor}
            sample = {"seconds": time.monotonic() - began, "groups": {}, "gpuBusyPercent": None}
            try:
                phase = (self.args.output / "native/pressure-phase").read_text().strip()
            except OSError:
                phase = "steady"
            sample["phase"] = phase
            for name, pids in groups.items():
                values = {str(pid): value for pid in pids if (value := counters(pid))}
                # Pressure workers live inside the native fixture's namespace,
                # but their synthetic CPU cost must not be called native overhead.
                if name == "native":
                    sample["groups"]["pressure"] = {
                        pid: value for pid, value in values.items() if value["name"] == "pressure"
                    }
                    values = {
                        pid: value for pid, value in values.items() if value["name"] != "pressure"
                    }
                sample["groups"][name] = values
            try:
                sample["gpuBusyPercent"] = int(
                    (
                        Path("/sys/class/drm")
                        / self.args.render_node.name
                        / "device/gpu_busy_percent"
                    ).read_text()
                )
            except (OSError, ValueError):
                pass
            self.samples.append(sample)

    def setup(self):
        args = self.args
        if args.method != "portal":
            config = args.output / "pipewire.conf"
            config.write_text(PIPEWIRE_CONFIG)
            self.start(["pipewire", "-c", config], "pipewire")
            self.wait(lambda: (self.run / "dpe-capture-test").exists())
            self.start(["wireplumber", "--profile=policy"], "policy")
            remote = self.run / "dpe-capture-test"
        else:
            remote = Path(self.host["XDG_RUNTIME_DIR"]) / "pipewire-0"
        if args.method == "game":
            broker = self.start(
                [
                    sys.executable,
                    args.game_bundle / "vkcapture-broker.py",
                    "--socket",
                    self.run / "game/hook.sock",
                    "--producer",
                    args.game_bundle / "dpe-vkcapture-source",
                    "--render-node",
                    "auto",
                ],
                "helper",
                pipe=True,
            )
            self.read(broker)
        source = [args.workload, "90", args.source_fps, "1920", "1080", "tiles"]
        if args.method == "game":
            source = [
                sys.executable,
                args.game_bundle / "gamecapture-launch.py",
                "--socket",
                self.run / "game/hook.sock",
                "--",
                *source,
            ]
        source_environment = dict(self.host, SDL_VIDEODRIVER=args.source_display)
        source_environment["DPE_GL_SEMAPHORE_SYNC"] = "1" if args.gl_semaphore_sync else "0"
        game = self.start(source, "workload", source_environment)
        window = None
        until = time.monotonic() + 6
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
                    if w.get("app_id") == "org.dpe.CapturePipelineBenchmark"
                    and (
                        w.get("pid") == game.pid
                        or (
                            args.source_display == "x11"
                            and w.get("title") == f"DPE capture pipeline benchmark [{game.pid}]"
                        )
                    )
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
        subprocess.run(
            [
                "niri",
                "msg",
                "action",
                "move-window-to-monitor",
                "--id",
                str(window["id"]),
                args.monitor,
            ],
            env=self.host,
            check=True,
            stdout=subprocess.DEVNULL,
            timeout=3,
        )
        time.sleep(0.2)
        windows = json.loads(
            subprocess.check_output(
                ["niri", "msg", "--json", "windows"], env=self.host, text=True, timeout=3
            )
        )
        moved = next((w for w in windows if w.get("id") == window["id"]), None)
        if moved is None:
            raise RuntimeError("Owned workload disappeared while moving to its monitor")
        if needs_fullscreen(moved):
            subprocess.run(
                ["niri", "msg", "action", "fullscreen-window", "--id", str(window["id"])],
                env=self.host,
                check=True,
                stdout=subprocess.DEVNULL,
                timeout=3,
            )
        time.sleep(0.8)
        if args.method == "portal":
            helper = self.start(
                [
                    "/usr/bin/python3",
                    ROOT / "tests/screenshare/compositor-session.py",
                    args.monitor,
                ],
                "portal",
                self.host,
                pipe=True,
            )
            ready = self.read(helper)
        elif args.method == "kms":
            inventory = json.loads(
                subprocess.check_output(
                    [args.kms_helper, "--list"], env=self.host, text=True, timeout=5
                )
            )
            selected = [
                entry
                for entry in inventory.get("outputs", [])
                if entry.get("name") == args.monitor and entry.get("supported") is True
            ]
            if inventory.get("available") is not True or len(selected) != 1:
                raise RuntimeError(
                    "The explicitly selected KMS monitor is unavailable or ambiguous"
                )
            helper = self.start(
                [
                    args.kms_helper,
                    "--output",
                    selected[0]["id"],
                    "--fps",
                    str(args.fps),
                    "--render-node",
                    args.render_node,
                ],
                "helper",
                pipe=True,
            )
            ready = self.read(helper)
        else:
            selected = None
            for i in range(40):
                self.command(broker, {"id": i, "op": "list"})
                message = self.read(broker)
                if message.get("sources"):
                    selected = message["sources"][0]
                    break
                time.sleep(0.05)
            if not selected:
                raise RuntimeError("Owned game hook missing")
            self.command(
                broker, {"id": "start", "op": "start", "token": selected["token"], "fps": args.fps}
            )
            until = time.monotonic() + 10
            ready = self.read(broker)
            while ready.get("event") != "ready":
                if time.monotonic() > until or ready.get("event") == "ended":
                    raise RuntimeError(f"Game capture did not start: {ready}")
                ready = self.read(broker)
        if ready.get("event") != "ready":
            raise RuntimeError(f"Capture did not start: {ready}")
        return remote, ready["nodeId"]

    def execute(self):
        remote, node = self.setup()
        self.thread = threading.Thread(target=self.monitor, daemon=True)
        self.thread.start()
        args = self.args
        check(
            runtime=args.runtime,
            voice=args.voice,
            output=args.output / "native",
            source="screen",
            motion="moving",
            fps=args.fps,
            seconds=args.seconds,
            drop_video=False,
            encoder=args.encoder,
            resolution="1920x1080",
            bitrate=6000000,
            render_node=args.render_node,
            source_fps=240,
            backend="pipewire",
            capture_mode="paced",
            memory="dmabuf",
            decoder="electron",
            quality_mode="fixed" if args.encode_resolution else "source",
            encode_resolution=args.encode_resolution,
            expect_resolution=args.encode_resolution or "1920x1080",
            display_backend="wayland",
            openh264=args.openh264,
            encoder_guard=True,
            transport_adapter=True,
            capture_scale=True,
            capture_sync=True,
            capture_picker=args.method != "portal" or args.delivery != "normal",
            capture_delivery=args.delivery,
            pipewire_socket=remote,
            pipewire_node=node,
            scenario="steady" if args.pressure == "none" else "pressure",
            pressure=args.pressure,
        )

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(3)
        for _, child in reversed(self.children):
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        for _, child in reversed(self.children):
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
            if child.stdout:
                pending = self.pending_lines.pop(child.pid, b"").decode(errors="replace")
                for line in (pending + child.stdout.read()).splitlines():
                    try:
                        self.events.append(json.loads(line))
                    except ValueError:
                        self.events.append({"invalidLine": line[:250]})
                child.stdout.close()
            if child.stdin:
                child.stdin.close()
        for log in self.logs:
            log.close()
        self.directory.cleanup()
        write_json(self.args.output / "producer-events.json", self.events)
        write_json(self.args.output / "resources.json", self.samples)
        write_json(
            self.args.output / "resource-summary.json",
            summarize_resources(self.samples, end=self.args.seconds - 2),
        )
        write_json(
            self.args.output / "resource-phases.json",
            {
                phase: summarize_resources(
                    [s for s in self.samples if s["phase"] == phase], start=0
                )
                for phase in sorted({s["phase"] for s in self.samples})
            },
        )
        write_json(
            self.args.output / "cleanup.json",
            {
                "children": {name: child.returncode for name, child in self.children},
                "cleaned": True,
            },
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--method", choices=("portal", "game", "kms"), required=True)
    for name in ("runtime", "voice", "workload", "render-node", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--game-bundle", type=Path)
    parser.add_argument("--kms-helper", type=Path)
    parser.add_argument("--game-handoff", choices=("copy", "relay"), default="copy")
    parser.add_argument("--delivery", choices=("normal", "probe", "fast"), default="normal")
    parser.add_argument("--gl-semaphore-sync", action="store_true")
    parser.add_argument("--openh264", type=Path)
    parser.add_argument("--monitor", required=True)
    parser.add_argument("--source-display", choices=("wayland", "x11"), default="wayland")
    parser.add_argument("--fps", type=int, choices=(30, 60), default=60)
    parser.add_argument("--seconds", type=int, choices=range(12, 31), default=20)
    parser.add_argument(
        "--source-fps",
        choices=("60", "120", "240", "600", "2000", "4000", "uncapped"),
        default="240",
    )
    parser.add_argument("--encoder", choices=("h264-vaapi", "av1-vaapi"), default="h264-vaapi")
    parser.add_argument("--encode-resolution", choices=("1280x720", "1920x1080"))
    parser.add_argument("--pressure", choices=("none", "cpu", "gpu", "both"), default="none")
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    for name in (
        "runtime",
        "voice",
        "workload",
        "render_node",
        "output",
        "game_bundle",
        "kms_helper",
        "openh264",
    ):
        if (value := getattr(args, name)) is not None:
            setattr(args, name, value.resolve())
    if args.method == "game" and not args.game_bundle:
        parser.error("Game capture requires an explicit --game-bundle")
    if args.method == "kms" and not args.kms_helper:
        parser.error("KMS capture requires an explicit --kms-helper")
    if args.gl_semaphore_sync and (args.method != "game" or args.game_handoff != "relay"):
        parser.error("GL semaphore experiments require game capture with relay handoff")
    if args.output.exists():
        parser.error("Choose a fresh output directory")
    files = [args.runtime, args.voice, args.workload]
    if args.kms_helper:
        files.append(args.kms_helper)
    if args.game_bundle:
        files += [
            args.game_bundle / name
            for name in (
                "vkcapture-broker.py",
                "gamecapture-launch.py",
                "dpe-vkcapture-source",
                "libobs_glcapture.so",
                "libVkLayer_obs_vkcapture.so",
            )
        ]
    if args.openh264:
        files.append(args.openh264)
    if any(not file.is_file() for file in files):
        parser.error("A required executable/module/bundle input is missing")
    plan = {
        key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()
    }
    plan["inputs"] = {str(file): sha256(file) for file in files}
    plan["scope"] = (
        "niri; owned fullscreen 1080p source; actual native local encoder/decoder; no account or saved pixels"
    )
    if not args.run:
        print(json.dumps(plan, indent=2))
        return
    if not os.environ.get("NIRI_SOCKET") or not os.environ.get("WAYLAND_DISPLAY"):
        parser.error(
            "This matched compositor runner currently requires the active niri Wayland session"
        )
    for program in ("niri", "pipewire", "wireplumber"):
        if not shutil.which(program):
            parser.error(f"Missing dependency: {program}")
    args.output.mkdir(parents=True)
    write_json(args.output / "plan.json", plan)
    run = Run(args)

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
        write_json(args.output / "result.json", {"status": status})
    print(args.output)


if __name__ == "__main__":
    main()
