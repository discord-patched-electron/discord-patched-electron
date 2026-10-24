#!/usr/bin/env python3
"""Private application-audio mix for Discord's native PID-based capture API.

Owns only its own PipeWire nodes, Pulse sink and child processes. stdin EOF or
SIGTERM closes the route. No default device, microphone or application playback
route is changed. PipeWire is required; PulseAudio may be the real server or its
PipeWire implementation. The protocol on stdin/stdout contains no audio data.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import select
import signal
import socket
import subprocess as sp
import sys
import time
import uuid
from functools import partial
from pathlib import Path


def child_guard(parent):
    # Close capture workers even if the Python owner is killed without cleanup.
    if ctypes.CDLL(None).prctl(1, signal.SIGTERM) != 0 or os.getppid() != parent:
        os._exit(1)


def command(args):
    return sp.check_output(args, text=True, timeout=3, stderr=sp.DEVNULL)


def process_identity(pid):
    if type(pid) is not int or pid <= 1:
        return None
    try:
        path = Path(f"/proc/{pid}")
        if path.stat().st_uid != os.getuid():
            return None
        fields = (path / "stat").read_text().rsplit(")", 1)[1].split()
        if fields[0] == "Z":
            return None
        return (pid, fields[19], int(fields[1]))
    except (OSError, ValueError, IndexError):
        return None


def descendant(pid, root):
    seen = set()
    while pid not in seen and len(seen) < 64:
        seen.add(pid)
        identity = process_identity(pid)
        if identity is None:
            return False
        if identity[:2] == root[:2]:
            return True
        pid = identity[2]
    return False


def discord_root(pid):
    root = process_identity(pid)
    if root is None:
        raise ValueError("Native owner process disappeared")
    # Electron's renderer/media host and main process normally use the same exe.
    # Do not walk into the user's shell or system service ancestry.
    while True:
        parent = process_identity(root[2])
        try:
            if parent is None or os.readlink(f"/proc/{parent[0]}/exe") != os.readlink(
                f"/proc/{root[0]}/exe"
            ):
                return root
        except OSError:
            return root
        root = parent


def integer(value):
    try:
        return int(value)
    except (ValueError, TypeError):
        return -1


def props(obj):
    return obj.get("info", {}).get("props", {})


def graph():
    return json.loads(command(["/usr/bin/pw-dump"]))


def video_sources(objects, owner):
    """Match links into this native process, not a guessed global active cast."""
    clients = {x["id"]: props(x) for x in objects if x["type"] == "PipeWire:Interface:Client"}
    nodes = {x["id"]: x for x in objects if x["type"] == "PipeWire:Interface:Node"}
    consumers = set()
    for node_id, node in nodes.items():
        p = props(node)
        client = clients.get(integer(p.get("client.id")), {})
        pid = integer(client.get("application.process.id", p.get("application.process.id")))
        # A portal can pass an already-open FD: pipewire.sec.pid then belongs
        # to the portal broker. The native client's advertised PID identifies
        # its video consumer; correlation still requires the actual graph link.
        if pid == owner and (
            p.get("media.class") == "Stream/Input/Video" or p.get("media.type") == "Video"
        ):
            consumers.add(node_id)
    sources = set()
    for item in objects:
        if item["type"] != "PipeWire:Interface:Link":
            continue
        info = item.get("info", {})
        if info.get("input-node-id") in consumers and info.get("state") in (
            "active",
            "paused",
        ):
            node = nodes.get(info.get("output-node-id"))
            if node:
                sources.add((node["id"], str(props(node).get("object.serial", ""))))
    return sources


def niri_request(request):
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(1)
        client.connect(os.environ["NIRI_SOCKET"])
        client.sendall((json.dumps(request) + "\n").encode())
        data = b""
        while b"\n" not in data:
            chunk = client.recv(65536)
            if not chunk or len(data) + len(chunk) > 4 * 1024 * 1024:
                raise ValueError("Invalid compositor reply")
            data += chunk
        response = json.loads(data.split(b"\n", 1)[0])
        return next(iter(response["Ok"].values()))


def niri_target(sources, casts, windows):
    if len(sources) != 1:
        return None
    node, serial = next(iter(sources))
    matches = [c for c in casts if c.get("pw_node_id") == node and c.get("is_active")]
    if len(matches) != 1:
        return None
    cast = matches[0]
    target = cast.get("target", {})
    key = (
        serial,
        cast["session_id"],
        cast["stream_id"],
        json.dumps(target, sort_keys=True),
    )
    if "Output" in target:
        return {"kind": "system", "key": key, "backend": "niri"}
    window_id = target.get("Window", {}).get("id")
    matches = [w for w in windows if w.get("id") == window_id and type(w.get("pid")) is int]
    if window_id is not None and len(matches) == 1:
        return {
            "kind": "application",
            "pid": matches[0]["pid"],
            "key": key,
            "backend": "niri",
        }
    return None


class Route:
    def __init__(self):
        self.children = []
        self.feeds = {}
        self.module = None
        self.prefix = "dpe-audio-" + uuid.uuid4().hex
        self.owner = None
        self.playback = None
        self.guard = None

    def spawn(self, args, **kwargs):
        # Native Discord's libpulse override must not replace the explicit,
        # independently chosen recorder/playback buffer sizes below.
        env = {k: v for k, v in os.environ.items() if k != "PULSE_LATENCY_MSEC"}
        child = sp.Popen(
            args,
            env=env,
            stdout=kwargs.pop("stdout", sp.DEVNULL),
            stderr=sp.DEVNULL,
            preexec_fn=partial(child_guard, os.getpid()),
            **kwargs,
        )
        self.children.append(child)
        return child

    @staticmethod
    def stop(child):
        if child.poll() is None:
            child.terminate()
        try:
            child.wait(timeout=1)
        except sp.TimeoutExpired:
            child.kill()
            child.wait()

    def create_sink(self, name):
        # Keep pw-cli connected: non-lingering objects are destroyed even if the
        # helper crashes and closes its stdin. Never create global permanent nodes.
        if self.owner is None:
            self.owner = self.spawn(["/usr/bin/pw-cli"], stdin=sp.PIPE)
        args = (
            "{ factory.name = support.null-audio-sink node.name = "
            + name
            + " media.class = Audio/Sink node.virtual = true priority.session = 0"
            " object.linger = false audio.position = [ FL FR ]"
            " adapter.auto-port-config = { mode = dsp monitor = true position = preserve } }"
        )
        self.owner.stdin.write(("create-node adapter " + args + "\n").encode())
        self.owner.stdin.flush()
        for _ in range(20):
            found = [
                x
                for x in graph()
                if x["type"] == "PipeWire:Interface:Node" and props(x).get("node.name") == name
            ]
            if len(found) == 1:
                return str(props(found[0])["object.serial"])
            time.sleep(0.05)
        raise ValueError("Private PipeWire sink did not appear")

    def pulse_playback(self):
        args = [
            "/usr/bin/pacat",
            "--playback",
            "--device=" + self.prefix + "-native",
            "--client-name=DPE-Private-Audio",
            "--stream-name=DPE-Private-Mix",
            "--raw",
            "--rate=48000",
            "--format=s16le",
            "--channels=2",
            "--latency-msec=80",
        ]
        if self.module is not None:
            # PulseAudio identifies the owning application through this property.
            # All workers belong to this helper; Discord can monitor every feed
            # with one PID without crossing audio-server clocks twice.
            args += ["--property=application.process.id=" + str(os.getpid())]
        return args

    def start(self):
        try:
            self.mix = self.create_sink(self.prefix + "-mix")
            server = json.loads(command(["/usr/bin/pactl", "-f", "json", "info"]))
            if "PipeWire" in server.get("server_name", ""):
                self.create_sink(self.prefix + "-native")
            else:
                self.module = int(
                    command(
                        [
                            "/usr/bin/pactl",
                            "load-module",
                            "module-null-sink",
                            "sink_name=" + self.prefix + "-native",
                            "rate=48000",
                            "channels=2",
                            "sink_properties=device.description=" + self.prefix + "-native",
                        ]
                    )
                )
                # Pulse modules outlive their creating client. A separate lease
                # process unloads ONLY this UUID-tagged module on stdin EOF,
                # including when the routing helper is killed with SIGKILL.
                self.guard = sp.Popen(
                    [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "--cleanup-module",
                        str(self.module),
                        "--sink-name",
                        self.prefix + "-native",
                    ],
                    stdin=sp.PIPE,
                    stdout=sp.DEVNULL,
                    stderr=sp.DEVNULL,
                )
            recording = self.spawn(
                [
                    "/usr/bin/pw-cat",
                    "--record",
                    "--target=" + self.mix,
                    "--properties={ stream.capture.sink = true node.passive = true node.dont-reconnect = true }",
                    "--raw",
                    "--rate=48000",
                    "--format=s16",
                    "--channels=2",
                    "--latency=80ms",
                    "-",
                ],
                stdout=sp.PIPE,
            )
            self.playback = self.spawn(self.pulse_playback(), stdin=recording.stdout)
            recording.stdout.close()
            self.capture_pid = os.getpid() if self.module is not None else self.playback.pid
            return self.capture_pid
        except Exception:
            self.close()
            raise

    def sync(self, objects, pulse, target, excluded):
        if not all(
            c.poll() is None
            for c in self.children
            if c not in [p for pair in self.feeds.values() for p in pair]
        ):
            raise ValueError("Audio bridge process exited")
        desired = {}
        sinks = None
        native_pulse_serials = set()
        clients = {x["id"]: props(x) for x in objects if x["type"] == "PipeWire:Interface:Client"}

        def wanted(pid):
            return (
                process_identity(pid) is not None
                and not descendant(pid, excluded)
                and not descendant(pid, process_identity(os.getpid()))
                and (target is None or descendant(pid, target))
            )

        for node in objects:
            if node["type"] != "PipeWire:Interface:Node":
                continue
            p = props(node)
            if p.get("media.class") != "Stream/Output/Audio" or str(
                p.get("node.name", "")
            ).startswith("dpe-audio-"):
                continue
            client = clients.get(integer(p.get("client.id")), {})
            pid = integer(p.get("application.process.id", client.get("application.process.id")))
            if not wanted(pid):
                continue
            serial = str(p.get("object.serial", ""))
            if not serial.isdecimal():
                continue
            native_pulse_serials.add(serial)
            desired[("pw", serial)] = [
                [
                    "/usr/bin/pw-loopback",
                    "-C",
                    serial,
                    "-P",
                    self.mix,
                    "-l",
                    "20",
                    "-i",
                    "{ stream.capture.sink = true node.passive = true node.dont-reconnect = true }",
                    "-o",
                    "{ node.passive = true node.dont-reconnect = true }",
                ]
            ]
        for stream in pulse:
            p = stream.get("properties", {})
            pid = integer(p.get("application.process.id"))
            identity = process_identity(pid)
            # pipewire-pulse exposes the same stream twice. Never mix it twice.
            if (
                identity is None
                or str(p.get("object.serial")) in native_pulse_serials
                or not wanted(pid)
            ):
                continue
            if "object.serial" in p:  # A PipeWire stream with no matching node is stale.
                continue
            index = integer(stream.get("index"))
            sink = integer(stream.get("sink"))
            if min(index, sink) < 0:
                continue
            if sinks is None:
                sinks = json.loads(command(["/usr/bin/pactl", "-f", "json", "list", "sinks"]))
            monitor = next(
                (
                    s.get("monitor_source_name", s.get("monitor_source"))
                    for s in sinks
                    if s["index"] == sink
                ),
                None,
            )
            # pactl runs out of process: an application can exit (or its PID
            # can be reused) while we obtain the monitor. Stay silent then.
            if monitor is None or process_identity(pid) != identity:
                continue
            desired[("pa", index, pid, identity[1], sink)] = [
                [
                    "/usr/bin/parec",
                    "--monitor-stream=" + str(index),
                    "--device=" + str(monitor),
                    "--raw",
                    "--rate=48000",
                    "--format=s16le",
                    "--channels=2",
                    "--latency-msec=20",
                ],
                self.pulse_playback(),
            ]
        for key in list(self.feeds):
            if key not in desired or any(p.poll() is not None for p in self.feeds[key]):
                for child in reversed(self.feeds.pop(key)):
                    self.stop(child)
                    self.children.remove(child)
        for key, commands in desired.items():
            if key in self.feeds:
                continue
            if len(commands) == 1:
                self.feeds[key] = [self.spawn(commands[0])]
            else:
                read = self.spawn(commands[0], stdout=sp.PIPE)
                write = self.spawn(commands[1], stdin=read.stdout)
                read.stdout.close()
                self.feeds[key] = [read, write]

    def close(self):
        for child in reversed(self.children):
            self.stop(child)
        self.children.clear()
        self.feeds.clear()
        if self.module is not None:
            try:
                command(["/usr/bin/pactl", "unload-module", str(self.module)])
            except (OSError, sp.SubprocessError):
                pass
            self.module = None
        if self.guard is not None:
            self.guard.stdin.close()
            try:
                self.guard.wait(timeout=5)
            except sp.TimeoutExpired:
                self.stop(self.guard)
            self.guard = None


class Controller:
    def __init__(self, owner):
        self.owner = owner
        self.owner_identity = process_identity(owner)
        self.excluded = discord_root(owner)
        self.generation = 0
        self.route = None
        self.target = None
        self.target_key = None
        self.enabled = False
        self.last = None
        self.hint = 0
        self.choice = None
        self.chooser = None
        self.choose_key = None

    def emit(self, pid=0, state="waiting", backend=None):
        value = {
            "generation": self.generation,
            "pid": pid,
            "state": state,
            "backend": backend,
        }
        if value != self.last:
            print(json.dumps(value), flush=True)
            self.last = value

    def reset(self, notify=True):
        try:
            if notify:
                self.emit()
        finally:
            if self.route:
                self.route.close()
            self.route = None
            self.target = None
            self.target_key = None
            if self.chooser:
                Route.stop(self.chooser)
            self.chooser = None
            self.choice = None
            self.choose_key = None

    def update(self, message):
        self.reset()
        self.generation = message["generation"]
        self.enabled = message.get("enabled") is True
        self.hint = integer(message.get("hint"))
        self.emit()

    def choose(self, objects, pulse, key):
        if key != self.choose_key:
            if self.chooser:
                Route.stop(self.chooser)
            self.choice = None
            self.choose_key = key
            choices = {}
            clients = {
                x["id"]: props(x) for x in objects if x["type"] == "PipeWire:Interface:Client"
            }
            entries = [
                {**clients.get(integer(props(x).get("client.id")), {}), **props(x)}
                for x in objects
                if x["type"] == "PipeWire:Interface:Node"
                and props(x).get("media.class") == "Stream/Output/Audio"
            ]
            entries += [
                s.get("properties", {})
                for s in pulse
                if "object.serial" not in s.get("properties", {})
            ]
            for entry in entries:
                pid = integer(entry.get("application.process.id"))
                identity = process_identity(pid)
                if identity and not descendant(pid, self.excluded):
                    label = str(
                        entry.get(
                            "application.name",
                            entry.get("application.process.binary", "Application"),
                        )
                    )[:120]
                    label = "".join(c for c in label if c.isprintable())
                    choices[pid] = (identity, label)
            self.choices = choices
            from window_inventory import desktop_note, windows

            inventory = windows()
            rows = ["system", "Entire system (except Discord)"]
            for pid, (identity, label) in sorted(choices.items()):
                owners = [
                    w
                    for w in inventory
                    if (root := process_identity(w["pid"])) and descendant(pid, root)
                ]
                titles = sorted({w["title"] for w in owners if w["title"]})
                detail = (
                    (" — " + titles[0])
                    if len(titles) == 1
                    else (f" — {len(titles)} windows" if titles else "")
                )
                rows.extend([str(pid), label + " (all audio)" + detail])
            self.chooser = sp.Popen(
                [
                    "/usr/bin/zenity",
                    "--list",
                    "--title=Screen-share audio",
                    "--text=Choose an application for this share. Audio from its other windows is included."
                    + ("\n\n" + desktop_note() if desktop_note() else ""),
                    "--column=Process",
                    "--column=Application",
                    "--hide-column=1",
                    "--print-column=1",
                    "--ok-label=Share audio",
                    "--cancel-label=No audio",
                    "--width=520",
                    "--height=360",
                    "--",
                    *rows,
                ],
                stdout=sp.PIPE,
                stderr=sp.DEVNULL,
                preexec_fn=partial(child_guard, os.getpid()),
            )
        if self.chooser and self.chooser.poll() is not None:
            child, self.chooser = self.chooser, None
            value = child.communicate()[0].decode().strip()
            if child.returncode == 0:
                if value == "system":
                    self.choice = {
                        "kind": "system",
                        "key": ("manual", *key),
                        "backend": "selection",
                    }
                elif integer(value) in self.choices:
                    identity = self.choices[integer(value)][0]
                    if process_identity(identity[0]) == identity:
                        self.choice = {
                            "kind": "application",
                            "pid": identity[0],
                            "key": ("manual", *key),
                            "backend": "selection",
                        }
        return self.choice

    def tick(self):
        if process_identity(self.owner) != self.owner_identity:
            raise EOFError()
        if not self.enabled:
            return
        objects = graph()
        pulse = json.loads(command(["/usr/bin/pactl", "-f", "json", "list", "sink-inputs"]))
        sources = video_sources(objects, self.owner)
        target = None
        niri_available = False
        if os.environ.get("NIRI_SOCKET"):
            try:
                target = niri_target(sources, niri_request("Casts"), niri_request("Windows"))
                niri_available = True
            except (OSError, ValueError, KeyError, StopIteration):
                pass  # Older niri versions use the explicit audio selector.
        # A real process hint is usable only for a single confirmed native video
        # stream. Numeric video node IDs are never used as process hints.
        if target is None and len(sources) == 1 and self.hint > 1:
            target = {
                "kind": "application",
                "pid": self.hint,
                "key": ("hint", *next(iter(sources))),
                "backend": "native-hint",
            }
        if target is None and len(sources) == 1 and not niri_available:
            target = self.choose(objects, pulse, next(iter(sources)))
        if target is None:
            if self.route:
                self.reset()
            self.emit()
            return
        identity = None if target["kind"] == "system" else process_identity(target["pid"])
        if target["kind"] != "system" and (identity is None or descendant(self.owner, identity)):
            self.reset()
            return
        key = (target["key"], identity)
        if self.target_key and self.target_key[0] == key[0] and self.target_key[1] != identity:
            # The window/session is unchanged but its process identity vanished
            # or was reused. Require a new video selection before following it.
            if self.route:
                self.route.close()
                self.route = None
            self.emit(state="unavailable")
            return
        if self.target_key != key:
            self.emit()
            if self.route:
                self.route.close()
            self.route = None
            self.target_key = key
            self.target = identity
            route = Route()
            route.start()
            self.route = route
        self.route.sync(objects, pulse, self.target, self.excluded)
        self.emit(self.route.capture_pid, "ready", target["backend"])

    def close(self):
        self.reset()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner", type=int)
    parser.add_argument("--cleanup-module", type=int)
    parser.add_argument("--sink-name")
    args = parser.parse_args()
    if args.cleanup_module is not None:
        sys.stdin.buffer.read()
        # pactl's JSON module format omits the numeric ID on some versions.
        # The stable tab-separated short format retains it. Validate both the
        # returned ID and our unique sink argument before unloading anything.
        modules = command(["/usr/bin/pactl", "list", "short", "modules"])
        for line in modules.splitlines():
            fields = line.split("\t")
            if (
                len(fields) >= 3
                and integer(fields[0]) == args.cleanup_module
                and fields[1] == "module-null-sink"
                and ("sink_name=" + str(args.sink_name)) in fields[2].split()
            ):
                command(["/usr/bin/pactl", "unload-module", str(args.cleanup_module)])
                break
        return
    if args.owner is None:
        parser.error("--owner is required")
    controller = Controller(args.owner)

    def stop(*_):
        raise EOFError()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    buffer = b""
    try:
        while True:
            if select.select([sys.stdin], [], [], 0.4)[0]:
                data = os.read(sys.stdin.fileno(), 8192)
                if not data:
                    break
                buffer += data
                if len(buffer) > 65536:
                    raise ValueError("Invalid controller message")
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    message = json.loads(line)
                    if type(message.get("generation")) is not int:
                        raise ValueError("Invalid generation")
                    controller.update(message)
            try:
                controller.tick()
            except (OSError, ValueError, KeyError, sp.SubprocessError) as error:
                if (
                    os.environ.get("DPE_APP_AUDIO_TRACE") == "1"
                    and (controller.last or {}).get("state") != "unavailable"
                ):
                    frame = error.__traceback__
                    while frame.tb_next:
                        frame = frame.tb_next
                    detail = {
                        "type": type(error).__name__,
                        "function": frame.tb_frame.f_code.co_name,
                        "line": frame.tb_lineno,
                    }
                    if isinstance(error, sp.CalledProcessError):
                        detail["command"] = Path(error.cmd[0]).name
                    print(
                        "[dpe-audio-helper-error] " + json.dumps(detail),
                        file=sys.stderr,
                        flush=True,
                    )
                controller.reset(notify=False)
                controller.emit(state="unavailable")
    except (EOFError, BrokenPipeError):
        pass
    finally:
        # A broken stdout or repeated termination must not prevent cleanup.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        try:
            controller.close()
        except BrokenPipeError:
            if controller.route:
                controller.route.close()


if __name__ == "__main__":
    main()
