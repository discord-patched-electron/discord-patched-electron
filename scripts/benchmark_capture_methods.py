"""Check an owned fullscreen source through portal, game, Wayland or KMS capture.

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
import stat
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


def descendants(pid, proc=Path("/proc"), *, cache=None):
    cache = {} if cache is None else cache
    result, pending = set(), [pid]
    while pending:
        parent = pending.pop()
        if parent not in cache:
            children = set()
            for file in (proc / str(parent) / "task").glob("*/children"):
                try:
                    children.update(map(int, file.read_text().split()))
                except (OSError, ValueError):
                    continue
            cache[parent] = children
        for child in cache[parent]:
            if child not in result:
                result.add(child)
                pending.append(child)
    return result


def drm_memory(data):
    """Keep memory regions/categories separate; shared imports are not additive."""
    result = {}
    for name, text in data.items():
        if not name.startswith(
            (
                "drm-total-",
                "drm-shared-",
                "drm-resident-",
                "drm-active-",
                "drm-purgeable-",
                "drm-memory-",
            )
        ) or name.startswith("drm-total-cycles-"):
            continue
        try:
            parts = text.split()
            if len(parts) not in (1, 2):
                continue
            multiplier = {"B": 1, "KiB": 1024, "MiB": 1024 * 1024}[
                parts[1] if len(parts) == 2 else "B"
            ]
            value = int(parts[0]) * multiplier
            if not 0 <= value < 1 << 64:
                continue
            result[name] = value
        except (ValueError, KeyError):
            continue
    # AMD's historical memory key aliases resident memory, not another pool.
    for name in list(result):
        if name.startswith("drm-memory-"):
            result.setdefault(name.replace("drm-memory-", "drm-resident-", 1), result.pop(name))
    return result


def device_memory(render_node, sysfs=Path("/sys/class/drm")):
    """Whole-device AMD counters; unavailable counters stay absent."""
    result = {}
    for name, file in {
        "vramUsedBytes": "mem_info_vram_used",
        "vramTotalBytes": "mem_info_vram_total",
        "gttUsedBytes": "mem_info_gtt_used",
        "gttTotalBytes": "mem_info_gtt_total",
    }.items():
        try:
            value = int((sysfs / Path(render_node).name / "device" / file).read_text())
            if 0 <= value < 1 << 64:
                result[name] = value
        except (OSError, ValueError):
            pass
    return result


def counters(pid, proc=Path("/proc")):
    try:
        process_stat = (proc / str(pid) / "stat").read_text()
        values = process_stat.rsplit(")", 1)[1].split()
        clients = {}
        memory = {}
        descriptors = proc / str(pid) / "fd"
        filter_devices = descriptors.is_dir()
        for fd in (proc / str(pid) / "fdinfo").glob("*"):
            try:
                if filter_devices:
                    device = (descriptors / fd.name).stat()
                    # DRM engine/memory fdinfo belongs to character-device
                    # files on Linux DRM major 226, not ordinary socket,
                    # eventfd, pipe or DMA-BUF fdinfo. Avoid parsing those.
                    if not stat.S_ISCHR(device.st_mode) or os.major(device.st_rdev) != 226:
                        continue
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
                if gpu_memory_values := drm_memory(data):
                    memory[key] = gpu_memory_values
            except (OSError, ValueError):
                continue
        return {
            "cpuTicks": int(values[11]) + int(values[12]),
            "startTicks": int(values[19]),
            "name": process_stat.split("(", 1)[1].rsplit(")", 1)[0],
            "nice": int(values[16]),
            "rssBytes": int(values[21]) * os.sysconf("SC_PAGE_SIZE") if len(values) > 21 else None,
            "drmClients": clients,
            "drmMemory": memory,
        }
    except (OSError, ValueError, IndexError):
        return None


def capture_threads(pid, proc=Path("/proc")):
    """Opt-in diagnostic counters; runqueue wait excludes blocked GPU/IO time."""
    result = {}
    for task in (proc / str(pid) / "task").glob("*"):
        try:
            stat = (task / "stat").read_text()
            name = stat.split("(", 1)[1].rsplit(")", 1)[0]
            if not (name.startswith("DPE ") or task.name == str(pid)):
                continue
            fields = stat.rsplit(")", 1)[1].split()
            scheduler = [int(value) for value in (task / "schedstat").read_text().split()]
            if len(scheduler) != 3 or any(value < 0 for value in scheduler):
                continue
            result[task.name] = {
                "name": name,
                "startTicks": int(fields[19]),
                "nice": int(fields[16]),
                "runningNs": scheduler[0],
                "runqueueWaitNs": scheduler[1],
                "timeslices": scheduler[2],
            }
        except (OSError, ValueError, IndexError):
            continue
    return result


def peer_identity(pid, proc=Path("/proc")):
    try:
        status = (proc / str(pid) / "status").read_text()
        nested = next(line.split()[1:] for line in status.splitlines() if line.startswith("NSpid:"))
        return {
            "pidNamespace": os.readlink(proc / str(pid) / "ns/pid"),
            "namespacePid": int(nested[-1]),
        }
    except (OSError, ValueError, IndexError, StopIteration):
        return {}


def memory_proportion(pid, proc=Path("/proc")):
    try:
        values = dict(
            line.split(":", 1)
            for line in (proc / str(pid) / "smaps_rollup").read_text().splitlines()
            if ":" in line
        )
        value, unit = values["Pss"].split()
        if unit != "kB" or int(value) < 0:
            return {}
        return {"pssBytes": int(value) * 1024}
    except (OSError, ValueError, KeyError):
        return {}


def split_peer_samples(samples, peers):
    """Namespace IDs never identify a host process without its namespace inode."""
    namespace = peers.get("pidNamespace")
    sender, receiver = peers.get("sender"), peers.get("receiver")
    if (
        not isinstance(namespace, str)
        or not namespace.startswith("pid:[")
        or type(sender) is not int
        or type(receiver) is not int
        or min(sender, receiver) <= 0
        or sender == receiver
    ):
        raise ValueError("Invalid independent peer identities")
    shared = {peers.get("browser"), *peers.get("sharedGpu", [])}
    observed = set()
    result = []
    for sample in samples:
        groups = dict(sample["groups"])
        native = groups.pop("native", {})
        for role in ("sender", "receiver", "shared", "other"):
            groups["native_" + role] = {}
        for pid, value in native.items():
            nested = value.get("namespacePid")
            role = "other"
            if value.get("pidNamespace") == namespace:
                if nested == sender:
                    role = "sender"
                elif nested == receiver:
                    role = "receiver"
                elif nested in shared:
                    role = "shared"
            groups["native_" + role][pid] = value
            observed.add(role)
        result.append(dict(sample, groups=groups))
    if not {"sender", "receiver"} <= observed:
        raise ValueError("Resource samples did not identify both independent peers")
    return result


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
                        previous = state["gpu"].setdefault((client, engine), [ns, ns])
                        previous[1] = max(previous[1], ns)
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
        # Sum resident pages at each instant, not per-process lifetime peaks.
        # Shared mappings can be counted in multiple processes: this is RSS,
        # not proportional/private memory or total physical GPU allocation.
        resident = [
            sum(value["rssBytes"] for value in processes.values())
            for sample in selected
            if (processes := sample["groups"].get(group))
            and all(value.get("rssBytes") is not None for value in processes.values())
        ]
        if resident:
            result[group]["rssMeanBytes"] = sum(resident) / len(resident)
            result[group]["rssPeakBytes"] = max(resident)
        proportional = []
        for sample in selected:
            if not sample.get("memorySnapshot", True):
                continue
            if not (processes := sample["groups"].get(group)):
                continue
            if not any("pssBytes" in value for value in processes.values()):
                continue
            lower = sum(value.get("pssBytes", 0) for value in processes.values())
            upper = sum(
                value.get("pssBytes", value.get("rssBytes", 0)) or 0 for value in processes.values()
            )
            if any(
                "pssBytes" not in value and value.get("rssBytes") is None
                for value in processes.values()
            ):
                continue
            proportional.append((lower, upper))
        if proportional:
            result[group]["pssSamples"] = len(proportional)
            result[group]["pssMeanLowerBytes"] = sum(p[0] for p in proportional) / len(proportional)
            result[group]["pssMeanUpperBytes"] = sum(p[1] for p in proportional) / len(proportional)
        memory_samples = {}
        for sample in selected:
            clients = {}
            for value in sample["groups"].get(group, {}).values():
                for client, metrics in value.get("drmMemory", {}).items():
                    target = clients.setdefault(client, {})
                    for metric, value in metrics.items():
                        target[metric] = max(target.get(metric, 0), value)
            totals = {}
            for metrics in clients.values():
                for metric, value in metrics.items():
                    totals[metric] = totals.get(metric, 0) + value
            for metric, value in totals.items():
                memory_samples.setdefault(metric, []).append(value)
        if memory_samples:
            result[group]["drmMemoryBytes"] = {
                metric: {
                    "mean": sum(values) / len(values),
                    "peak": max(values),
                    "samples": len(values),
                }
                for metric, values in memory_samples.items()
            }
            result[group]["drmMemoryScope"] = (
                "Per-client memory, deduplicated within this group; shared buffers can occur in other clients/groups. Categories and regions must not be added as physical memory."
            )
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


def direct_command(args, output_id):
    helper = args.kms_helper if args.method == "kms" else args.wayland_helper
    command = [helper, "--output", output_id, "--fps", str(args.fps)]
    if args.method == "kms":
        command += ["--render-node", args.render_node]
    return command


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
            DPE_CAPTURE_PROFILE="1" if getattr(args, "profile_pipeline", False) else "0",
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
        began = active_began = None
        memory_at = memory_phase = None
        memory_interval = getattr(self.args, "memory_sample_seconds", 2.0)
        sampling_cpu = 0
        try:
            inherited_nice = os.getpriority(os.PRIO_PROCESS, 0)
            os.setpriority(os.PRIO_PROCESS, 0, max(inherited_nice, 10))
            sampling_nice = os.getpriority(os.PRIO_PROCESS, 0)
        except OSError:
            sampling_nice = None
        while not self.stop.wait(0.5):
            active = (self.args.output / "native/capture-started").exists()
            if not active and not (self.args.output / "native/baseline-started").exists():
                continue
            now = time.monotonic()
            sample_cpu_started = time.thread_time_ns()
            if began is None:
                began = now
            if active and active_began is None:
                active_began = now
            children = {}
            groups = {
                name: {child.pid} | descendants(child.pid, cache=children)
                for name, child in self.children
            }
            assigned = set().union(*groups.values())
            groups["native"] = descendants(os.getpid(), cache=children) - assigned
            groups["compositor"] = {self.compositor}
            sample = {
                "seconds": now - (active_began if active else began),
                "monotonic": now,
                "groups": {},
                "gpuBusyPercent": None,
            }
            try:
                phase = (self.args.output / "native/pressure-phase").read_text().strip()
            except OSError:
                phase = "steady"
            sample["phase"] = phase if active else "baseline"
            memory_snapshot = (
                memory_at is None
                or memory_phase != sample["phase"]
                or now - memory_at >= memory_interval
            )
            if memory_snapshot:
                memory_at, memory_phase = now, sample["phase"]
            sample["memorySnapshot"] = memory_snapshot
            for name, pids in groups.items():
                values = {str(pid): value for pid in pids if (value := counters(pid))}
                if getattr(self.args, "split_peers", False):
                    for pid, value in values.items():
                        if memory_snapshot:
                            value.update(memory_proportion(pid))
                        if name == "native":
                            value.update(peer_identity(pid))
                if getattr(self.args, "profile_pipeline", False):
                    for pid, value in values.items():
                        value["threads"] = capture_threads(pid)
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
            if values := device_memory(self.args.render_node):
                sample["deviceMemoryBytes"] = values
            sample_cpu = time.thread_time_ns() - sample_cpu_started
            sampling_cpu += sample_cpu
            sample["observer"] = {
                "samplingCpuNs": sampling_cpu,
                "sampleCpuNs": sample_cpu,
                "sampleWallSeconds": time.monotonic() - now,
                "processCpuNs": time.process_time_ns(),
                "nice": sampling_nice,
                "memorySnapshotIntervalSeconds": memory_interval,
                "scope": "Benchmark observer only; excluded from capture process totals",
            }
            self.samples.append(sample)

    def setup(self):
        args = self.args
        direct = args.capture_transport == "direct-gpu"
        if direct:
            remote = None
        elif args.method != "portal":
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
        source_environment["DPE_CAPTURE_PROFILE"] = (
            "1" if getattr(args, "profile_pipeline", False) else "0"
        )
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
        subprocess.run(
            ["niri", "msg", "action", "focus-window", "--id", str(window["id"])],
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
        elif args.method in ("kms", "wayland"):
            executable = args.kms_helper if args.method == "kms" else args.wayland_helper
            inventory = json.loads(
                subprocess.check_output([executable, "--list"], env=self.host, text=True, timeout=5)
            )
            selected = [
                entry
                for entry in inventory.get("outputs", [])
                if entry.get("name") == args.monitor and entry.get("supported") is True
            ]
            if inventory.get("available") is not True or len(selected) != 1:
                raise RuntimeError(
                    "The explicitly selected direct monitor is unavailable or ambiguous"
                )
            helper = self.start(
                [
                    args.direct_helper,
                    "--output",
                    selected[0]["id"],
                    "--fps",
                    str(args.fps),
                    "--render-node",
                    args.render_node,
                ]
                if direct
                else direct_command(args, selected[0]["id"]),
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
                broker,
                {
                    "id": "start",
                    "op": "start",
                    "token": selected["token"],
                    "fps": args.fps,
                    **({"transport": "direct-gpu"} if direct else {}),
                },
            )
            until = time.monotonic() + 10
            ready = self.read(broker)
            while ready.get("event") != "ready":
                if time.monotonic() > until or ready.get("event") == "ended":
                    raise RuntimeError(f"Game capture did not start: {ready}")
                ready = self.read(broker)
        if ready.get("event") != "ready":
            raise RuntimeError(f"Capture did not start: {ready}")
        if direct:
            if ready.get("transport") != "direct-gpu" or ready.get("nodeId") != 0xFFFFFFFE:
                raise RuntimeError("The producer did not establish direct GPU transport")
            remote = Path(ready["socketPath"])
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
            bitrate=args.bitrate,
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
            capture_transport=args.capture_transport,
            pipewire_socket=remote,
            pipewire_node=node,
            scenario="steady" if args.pressure == "none" else "pressure",
            pressure=args.pressure,
            profile_pipeline=args.profile_pipeline,
            capture_priority=getattr(args, "capture_priority", "inherit"),
            split_peers=getattr(args, "split_peers", False),
            baseline_seconds=getattr(args, "baseline_seconds", 0),
        )
        if getattr(args, "split_peers", False):
            peers = json.loads((args.output / "native/peer-processes.json").read_text())
            split_peer_samples(self.samples[:], peers)  # Fail before claiming usable resources.

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
        if getattr(self.args, "split_peers", False):
            peers_file = self.args.output / "native/peer-processes.json"
            if peers_file.is_file():
                try:
                    self.samples = split_peer_samples(
                        self.samples, json.loads(peers_file.read_text())
                    )
                except ValueError:
                    pass  # Failed setup retains original observations for diagnosis.
        write_json(self.args.output / "producer-events.json", self.events)
        write_json(self.args.output / "resources.json", self.samples)
        write_json(
            self.args.output / "resource-summary.json",
            summarize_resources(
                [s for s in self.samples if s["phase"] != "baseline"], end=self.args.seconds - 2
            ),
        )
        write_json(
            self.args.output / "resource-phases.json",
            {
                phase: summarize_resources(
                    [s for s in self.samples if s["phase"] == phase],
                    start=1 if phase == "baseline" else 0,
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


def producer_failures(events):
    """A delivery pass cannot hide the producer's final failure report."""
    return [
        event
        for event in events
        if event.get("failed") is True
        or event.get("errorCode")
        or (
            event.get("event") == "stopped"
            and isinstance(event.get("code"), int)
            and event["code"] > 0
        )
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--method", choices=("portal", "game", "kms", "wayland"), required=True)
    for name in ("runtime", "voice", "workload", "render-node", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--game-bundle", type=Path)
    parser.add_argument("--kms-helper", type=Path)
    parser.add_argument("--wayland-helper", type=Path)
    parser.add_argument("--direct-helper", type=Path)
    parser.add_argument(
        "--capture-transport", choices=("pipewire", "direct-gpu"), default="pipewire"
    )
    parser.add_argument("--game-handoff", choices=("copy", "relay"), default="copy")
    parser.add_argument("--delivery", choices=("normal", "probe", "fast"), default="normal")
    parser.add_argument(
        "--gl-semaphore-sync",
        action="store_true",
        help="Request the GL bridge; forced-copy hook allocation does not activate it",
    )
    parser.add_argument("--openh264", type=Path)
    parser.add_argument("--monitor", required=True)
    parser.add_argument("--source-display", choices=("wayland", "x11"), default="wayland")
    parser.add_argument("--fps", type=int, choices=(30, 60, 120, 240), default=60)
    parser.add_argument(
        "--bitrate", type=int, choices=(6000000, 12000000, 24000000), default=6000000
    )
    parser.add_argument("--seconds", type=int, choices=range(12, 31), default=20)
    parser.add_argument(
        "--source-fps",
        choices=("60", "120", "240", "600", "2000", "4000", "uncapped"),
        default="240",
    )
    parser.add_argument(
        "--encoder", choices=("h264-vaapi", "h265-vaapi", "av1-vaapi"), default="h264-vaapi"
    )
    parser.add_argument("--encode-resolution", choices=("1280x720", "1920x1080"))
    parser.add_argument("--pressure", choices=("none", "cpu", "gpu", "both"), default="none")
    parser.add_argument("--run", action="store_true")
    parser.add_argument(
        "--profile-pipeline",
        action="store_true",
        help="Record inclusive native CPU/wall stage timings",
    )
    parser.add_argument(
        "--split-peers", action="store_true", help="Separate sender/receiver renderer processes"
    )
    parser.add_argument("--baseline-seconds", type=int, default=0, choices=range(11))
    parser.add_argument(
        "--memory-sample-seconds",
        type=float,
        default=2.0,
        choices=(0.5, 1.0, 2.0, 5.0),
        help="PSS snapshot interval; CPU/RSS/DRM counters remain sampled every 0.5 seconds",
    )
    parser.add_argument("--capture-priority", choices=("inherit", "balanced"), default="inherit")
    args = parser.parse_args()
    if args.openh264 and args.encoder != "h264-vaapi":
        parser.error("OpenH264 is an H.264 decoder; omit it for AV1/HEVC benchmarks")
    for name in (
        "runtime",
        "voice",
        "workload",
        "render_node",
        "output",
        "game_bundle",
        "kms_helper",
        "wayland_helper",
        "direct_helper",
        "openh264",
    ):
        if (value := getattr(args, name)) is not None:
            setattr(args, name, value.resolve())
    if args.method == "game" and not args.game_bundle:
        parser.error("Game capture requires an explicit --game-bundle")
    if args.method == "kms" and not args.kms_helper:
        parser.error("KMS capture requires an explicit --kms-helper")
    if args.method == "wayland" and not args.wayland_helper:
        parser.error("Wayland capture requires an explicit --wayland-helper")
    if args.capture_transport == "direct-gpu":
        if args.method not in ("game", "kms") or not args.direct_helper:
            parser.error("Direct GPU transport requires game/KMS and --direct-helper")
        if args.delivery != "normal":
            parser.error("Direct GPU transport owns its delivery; use --delivery normal")
        if args.method == "game":
            if args.direct_helper != args.game_bundle.parent / "dpe-direct-source":
                parser.error("The direct helper must belong to the selected game bundle")
            args.gl_semaphore_sync = True
        elif args.direct_helper.parent != args.kms_helper.parent:
            parser.error("The direct and KMS helpers must belong to the same capture bundle")
    if args.gl_semaphore_sync and args.method != "game":
        parser.error("GL semaphore experiments require game capture")
    if args.output.exists():
        parser.error("Choose a fresh output directory")
    files = [args.runtime, args.voice, args.workload]
    if args.kms_helper:
        files.append(args.kms_helper)
    if args.wayland_helper:
        files.append(args.wayland_helper)
    if args.direct_helper:
        files.append(args.direct_helper)
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
    programs = (
        ("niri",) if args.capture_transport == "direct-gpu" else ("niri", "pipewire", "wireplumber")
    )
    for program in programs:
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
        failures = producer_failures(run.events)
        if failures:
            status = "failed"
        write_json(args.output / "result.json", {"status": status, "producerFailures": failures})
    if status != "passed":
        raise RuntimeError("Capture producer failed; see producer-events.json")
    print(args.output)


if __name__ == "__main__":
    main()
