"""Run one bounded game-capture lifecycle through the actual native receiver.

Use the same arguments as benchmark_capture_methods.py, with --method game,
--fps 60, --seconds 20 or longer and --pressure none. Only the owned workload is
resized or paused. This tests rate renegotiation, geometry changes and recovery;
it does not recreate the game's graphics context at the same dimensions.
"""

import json
import math
import selectors
import signal
import subprocess
import threading
import time

import benchmark_capture_methods as benchmark
from common import write_json

ACTIONS = (
    (3.0, "rate30"),
    (6.0, "rate60"),
    (9.0, "resized"),
    (11.0, "restored"),
    (13.0, "paused"),
    (14.2, "recovered"),
)


def frame_clock_ms(frame, anchor_ms):
    """Unwrap the shared CLOCK_MONOTONIC marker, then reconstruct receipt time."""
    period = 1 << 24
    source = anchor_ms + ((frame["sequence"] - anchor_ms + period / 2) % period - period / 2)
    return source + frame["latencyMs"]


def distribution(values):
    ordered = sorted(values)
    if not ordered:
        return {"count": 0}
    return {
        "count": len(ordered),
        **{
            name: ordered[min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1)]
            for name, fraction in (("p50", 0.50), ("p95", 0.95), ("p99", 0.99))
        },
        "max": ordered[-1],
    }


def summarize(report, events, seconds):
    actions = [event for event in events if event.get("kind") == "action"]
    if not actions:
        return {"passed": False, "error": "No lifecycle actions observed"}
    origin = actions[0]["monotonicMs"]
    frames = [
        dict(frame, receiptMs=frame_clock_ms(frame, origin))
        for frame in report.get("measurements", {}).get("frames", [])
    ]
    phases = {}
    for index, event in enumerate(actions):
        end = (
            actions[index + 1]["monotonicMs"]
            if index + 1 < len(actions)
            else origin + seconds * 1000
        )
        grace = (
            1000
            if index == 0 or event["name"] == "recovered"
            else 300
            if event["name"] == "paused"
            else 500
        )
        start = event["monotonicMs"] + grace
        selected = [frame for frame in frames if start <= frame["receiptMs"] < end]
        duration = max(0, (end - start) / 1000)
        phases[event["name"]] = {
            "measuredSeconds": duration,
            "uniqueFrames": len(selected),
            "uniqueFps": len(selected) / duration if duration else None,
            "dimensions": sorted({f"{f['width']}x{f['height']}" for f in selected}),
            "latencyMs": distribution([frame["latencyMs"] for frame in selected]),
            "intervalMs": distribution(
                [
                    b["receiptMs"] - a["receiptMs"]
                    for a, b in zip(selected, selected[1:], strict=False)
                ]
            ),
        }
    ready = [
        event
        for event in events
        if event.get("kind") == "producer" and event.get("event") == "ready"
    ]
    ended = [
        event
        for event in events
        if event.get("kind") == "producer" and event.get("event") == "ended"
    ]
    replies = [
        event
        for event in events
        if event.get("kind") == "producer"
        and event.get("id") in ("lifecycle-rate30", "lifecycle-rate60")
    ]
    dimensions = {value for phase in phases.values() for value in phase["dimensions"]}

    def fps(name):
        return phases.get(name, {}).get("uniqueFps") or 0

    checks = {
        "allActions": [event["name"] for event in actions]
        == ["initial", *(name for _, name in ACTIONS)],
        "actionsSucceeded": not any(event.get("kind") == "error" for event in events),
        "nativePassed": report.get("passed") is True,
        "sameNode": bool(ready) and len({event.get("nodeId") for event in ready}) == 1,
        "ratesAcknowledged": len(replies) == 2
        and all(event.get("ok") is True for event in replies),
        "initialCadence": fps("initial") >= 45,
        "rate30Cadence": 20 <= fps("rate30") <= 36,
        "rate60Cadence": fps("rate60") >= 45,
        "resizeObserved": len(dimensions) >= 2
        and any(value != "1920x1080" for value in phases.get("resized", {}).get("dimensions", [])),
        "resizedCadence": fps("resized") >= 30,
        "restoredDimensions": phases.get("restored", {}).get("dimensions") == ["1920x1080"],
        "restoredCadence": fps("restored") >= 45,
        "pauseObserved": phases.get("paused", {}).get("uniqueFrames", 999) <= 2,
        "recoveredCadence": fps("recovered") >= 45,
        "recoveredDimensions": phases.get("recovered", {}).get("dimensions") == ["1920x1080"],
        "producerCleanEnd": bool(ended) and ended[-1].get("failed") is False,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "phases": phases,
        "sameSizeGameTextureReplacementCovered": False,
        "clock": "Source CLOCK_MONOTONIC millisecond marker unwrapped around action time; latency reconstructs receipt time, approximately 1 ms precision",
        "scope": "One native receiver session; own window fullscreen off/on, broker rate changes and 1.2-second source-process pause",
    }


class LifecycleRun(benchmark.Run):
    def __init__(self, args):
        if (
            args.method != "game"
            or args.fps != 60
            or args.seconds < 20
            or args.pressure != "none"
            or args.encode_resolution
        ):
            raise ValueError(
                "Lifecycle needs game capture, 60 FPS, >=20 seconds, Source quality and no pressure"
            )
        super().__init__(args)
        self.lifecycle = []
        self.paused = False
        self.workload = self.broker = None
        self.window_id = None
        plan_path = args.output / "plan.json"
        if plan_path.is_file():
            plan = json.loads(plan_path.read_text())
            plan["lifecycle"] = [{"seconds": at, "action": name} for at, name in ACTIONS]
            plan["scope"] = (
                "One native sender/receiver; owned game rate changes, fullscreen resize and bounded process pause; no same-size game-texture recreation"
            )
            write_json(plan_path, plan)

    def setup(self):
        result = super().setup()
        children = dict(self.children)
        self.workload, self.broker = children["workload"], children["helper"]
        windows = json.loads(
            subprocess.check_output(
                ["niri", "msg", "--json", "windows"], env=self.host, text=True, timeout=3
            )
        )
        owned = next(
            (
                w
                for w in windows
                if w.get("app_id") == "org.dpe.CapturePipelineBenchmark"
                and (
                    w.get("pid") == self.workload.pid
                    or w.get("title") == f"DPE capture pipeline benchmark [{self.workload.pid}]"
                )
            ),
            None,
        )
        if not owned:
            raise RuntimeError("Owned workload window missing after setup")
        self.window_id = owned["id"]
        self.lifecycle += [
            dict(event, kind="producer", monotonicMs=time.monotonic() * 1000)
            for event in self.events
            if event.get("event") == "ready"
        ]
        return result

    def action(self, name):
        event = {"kind": "action", "name": name, "monotonicMs": time.monotonic() * 1000}
        if name in ("rate30", "rate60"):
            self.command(
                self.broker, {"id": f"lifecycle-{name}", "op": "rate", "fps": int(name[4:])}
            )
        elif name in ("resized", "restored"):
            subprocess.run(
                ["niri", "msg", "action", "fullscreen-window", "--id", str(self.window_id)],
                env=self.host,
                check=True,
                stdout=subprocess.DEVNULL,
                timeout=3,
            )
        elif name == "paused":
            self.workload.send_signal(signal.SIGSTOP)
            self.paused = True
        elif name == "recovered":
            self.workload.send_signal(signal.SIGCONT)
            self.paused = False
        self.lifecycle.append(event)

    def collect(self):
        while True:
            pending = b"\n" in self.pending_lines.get(self.broker.pid, b"")
            with selectors.DefaultSelector() as selector:
                selector.register(self.broker.stdout, selectors.EVENT_READ)
                available = bool(selector.select(0))
            if not pending and not available:
                return
            value = self.read(self.broker, 0.2)
            self.lifecycle.append(dict(value, kind="producer", monotonicMs=time.monotonic() * 1000))
            if value.get("event") == "ended":
                return

    def monitor(self):
        resources = threading.Thread(target=super().monitor, daemon=True)
        resources.start()
        origin = None
        index = 0
        try:
            while not self.stop.wait(0.025):
                if not (self.args.output / "native/capture-started").exists():
                    continue
                if origin is None:
                    origin = time.monotonic()
                    self.action("initial")
                self.collect()
                if index < len(ACTIONS) and time.monotonic() - origin >= ACTIONS[index][0]:
                    self.action(ACTIONS[index][1])
                    index += 1
        except Exception as error:
            self.lifecycle.append(
                {"kind": "error", "error": str(error), "monotonicMs": time.monotonic() * 1000}
            )
        finally:
            if self.paused:
                self.workload.send_signal(signal.SIGCONT)
                self.paused = False
            resources.join(3)

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(3)
        if self.paused and self.workload and self.workload.poll() is None:
            self.workload.send_signal(signal.SIGCONT)
            self.paused = False
        if self.broker and self.broker.poll() is None:
            try:
                self.command(self.broker, {"id": "lifecycle-stop", "op": "stop"})
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    self.collect()
                    if any(event.get("event") == "ended" for event in self.lifecycle):
                        break
                    time.sleep(0.02)
            except (OSError, RuntimeError, ValueError) as error:
                self.lifecycle.append(
                    {"kind": "error", "error": str(error), "monotonicMs": time.monotonic() * 1000}
                )
        before = len(self.events)
        super().close()
        self.lifecycle += [
            dict(event, kind="producer", monotonicMs=time.monotonic() * 1000)
            for event in self.events[before:]
            if event.get("event") == "ended"
        ]
        write_json(self.args.output / "lifecycle-events.json", self.lifecycle)
        path = self.args.output / "native/report.json"
        report = json.loads(path.read_text()) if path.is_file() else {}
        summary = summarize(report, self.lifecycle, self.args.seconds)
        workload = self.args.output / "workload.log"
        if workload.is_file():
            for line in workload.read_text(errors="replace").splitlines():
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                if value.get("event") == "finished":
                    summary["workload"] = value
        write_json(self.args.output / "lifecycle-report.json", summary)
        if not summary["passed"]:
            write_json(self.args.output / "result.json", {"status": "failed", "lifecycle": False})
            raise RuntimeError(
                f"Lifecycle validation failed; inspect {self.args.output}/lifecycle-report.json"
            )


def main():
    original_check = benchmark.check

    def changing_geometry_check(**options):
        # Mixed dimensions are intentional here; lifecycle checks require both
        # the transition and recovery to the original 1080p dimensions.
        options["expect_resolution"] = None
        return original_check(**options)

    benchmark.Run = LifecycleRun
    benchmark.check = changing_geometry_check
    benchmark.main()


if __name__ == "__main__":
    main()
