"""Owned PipeWire/Pulse notifications; no PCM data or routing changes here."""

import codecs
import json
import os
import select
import subprocess
import time


class GraphState:
    """Merge pw-dump's incremental arrays, retaining only routing metadata."""

    TYPES = {
        "PipeWire:Interface:Node",
        "PipeWire:Interface:Client",
        "PipeWire:Interface:Link",
    }
    FIELDS = {"props", "state", "input-node-id", "output-node-id"}

    def __init__(self):
        self.objects = {}
        self.buffer = ""
        self.decoder = codecs.getincrementaldecoder("utf-8")()
        self.json = json.JSONDecoder()
        self.ready = False

    @property
    def pending(self):
        return bool(self.buffer.strip() or self.decoder.getstate()[0])

    def feed(self, chunk):
        self.buffer += self.decoder.decode(chunk)
        if len(self.buffer) > 8 * 1024 * 1024:
            raise ValueError("PipeWire graph message exceeds its bound")
        while self.buffer.strip():
            self.buffer = self.buffer.lstrip()
            try:
                values, end = self.json.raw_decode(self.buffer)
            except json.JSONDecodeError:
                return
            if not isinstance(values, list) or len(values) > 8192:
                raise ValueError("Invalid PipeWire graph update")
            for value in values:
                if not isinstance(value, dict) or type(value.get("id")) is not int:
                    raise ValueError("Invalid PipeWire object")
                identity = value["id"]
                if not 0 <= identity <= 0xFFFFFFFF:
                    raise ValueError("Invalid PipeWire object identity")
                if "info" in value and value["info"] is None:
                    self.objects.pop(identity, None)
                    continue
                previous = self.objects.get(identity, {})
                kind = value.get("type", previous.get("type"))
                if kind not in self.TYPES:
                    self.objects.pop(identity, None)
                    continue
                if kind != previous.get("type"):
                    previous = {}
                info = value.get("info", {})
                if not isinstance(info, dict):
                    raise ValueError("Invalid PipeWire object info")
                updated = {key: data for key, data in info.items() if key in self.FIELDS}
                if "props" in updated and not isinstance(updated["props"], dict):
                    raise ValueError("Invalid PipeWire properties")
                # A props update replaces its entire property set. Absent info
                # fields retain their previous value; removed properties do not.
                self.objects[identity] = {
                    "id": identity,
                    "type": kind,
                    "info": {**previous.get("info", {}), **updated},
                }
            if len(self.objects) > 8192:
                raise ValueError("PipeWire graph exceeds its bound")
            self.ready = True
            self.buffer = self.buffer[end:]


class PulseEvents:
    def __init__(self):
        self.buffer = b""
        self.ready = False
        self.inputs_dirty = True
        self.sinks_dirty = True

    def feed(self, chunk):
        self.buffer += chunk
        if len(self.buffer) > 65536:
            raise ValueError("PulseAudio event exceeds its bound")
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            if not line.startswith(b"Event '") or b" on " not in line:
                raise ValueError("Invalid PulseAudio subscription event")
            self.ready = True
            facility = line.split(b" on ", 1)[1].split(b" ", 1)[0]
            if facility in {b"sink-input", b"sink", b"source", b"server", b"card"}:
                self.inputs_dirty = True
            if facility in {b"sink", b"source", b"server", b"card"}:
                self.sinks_dirty = True
            # Introspection connects a short-lived client. Its own client events
            # establish subscription readiness but never invalidate the cache.


class AudioGraph:
    def __init__(self, launch, command):
        self.launch, self.command = launch, command
        self.children = []
        self.graph = GraphState()
        self.pulse = PulseEvents()
        self.inputs = self.sinks = None

    def filenos(self):
        return [child.stdout for child in self.children if child.poll() is None]

    def _start(self):
        if self.children:
            return
        try:
            for args in (["/usr/bin/pw-dump", "-m", "-N"], ["/usr/bin/pactl", "subscribe"]):
                child = self.launch(args)
                self.children.append(child)
                os.set_blocking(child.stdout.fileno(), False)
        except Exception:
            self.close()
            raise

    def pump(self):
        if any(child.poll() is not None for child in self.children):
            self.close()
            raise ValueError("Audio graph subscription ended")
        try:
            for index, child in enumerate(self.children):
                # Bound each drain even if another client continuously changes
                # the graph. Stdin and owner-exit checks still get serviced.
                for _ in range(128):
                    try:
                        chunk = os.read(child.stdout.fileno(), 65536)
                    except BlockingIOError:
                        break
                    if not chunk:
                        raise ValueError("Audio graph subscription closed")
                    (self.graph if index == 0 else self.pulse).feed(chunk)
        except (OSError, ValueError, UnicodeError, RecursionError):
            self.close()
            raise ValueError("Invalid audio graph subscription") from None

    def _ready(self):
        self._start()
        self.pump()
        deadline = time.monotonic() + 3
        while not self.graph.ready or self.graph.pending or not self.pulse.ready:
            self.pump()
            if self.graph.ready and not self.graph.pending and self.pulse.ready:
                break
            if time.monotonic() >= deadline:
                self.close()
                raise ValueError("Audio graph subscription did not become ready")
            if not self.pulse.ready:
                # A read-only client connection produces a subscription event.
                # Observe one before taking the initial snapshot, so a listener
                # startup race cannot miss a stream created during that snapshot.
                self.command(["/usr/bin/pactl", "info"])
            select.select(self.filenos(), [], [], 0.05)

    def snapshot(self):
        self._ready()
        if self.inputs is None or self.pulse.inputs_dirty:
            self.inputs = json.loads(
                self.command(["/usr/bin/pactl", "-f", "json", "list", "sink-inputs"])
            )
            if not isinstance(self.inputs, list):
                raise ValueError("Invalid PulseAudio inputs")
            self.pulse.inputs_dirty = False
        return list(self.graph.objects.values()), self.inputs

    def sink_snapshot(self):
        self._ready()
        if self.sinks is None or self.pulse.sinks_dirty:
            self.sinks = json.loads(self.command(["/usr/bin/pactl", "-f", "json", "list", "sinks"]))
            if not isinstance(self.sinks, list):
                raise ValueError("Invalid PulseAudio sinks")
            self.pulse.sinks_dirty = False
        return self.sinks

    def close(self):
        for child in self.children:
            if child.poll() is None:
                child.terminate()
        for child in self.children:
            try:
                child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
            child.stdout.close()
        self.children.clear()
        self.graph = GraphState()
        self.pulse = PulseEvents()
        self.inputs = self.sinks = None
