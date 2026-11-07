"""Control bounded pressure children belonging only to the private fixture."""

import json
import os
import subprocess
import time
from pathlib import Path


class Pressure:
    def __init__(self, root, config, environment):
        if config.get("pressure") not in ("cpu", "gpu", "both"):
            raise ValueError("Unknown pressure mode")
        if (
            config.get("backend") != "pipewire"
            or type(config.get("seconds")) is not int
            or not 12 <= config["seconds"] <= 30
        ):
            raise ValueError("Pressure requires a bounded private PipeWire session")
        if config["pressure"] in ("gpu", "both") and not config.get("render_node"):
            raise ValueError("GPU pressure requires an explicit render node")
        self.root, self.config, self.environment = root, config, environment
        self.children, self.logs, self.events, self.samples = [], [], [], []
        self.phase, self.next_sample = "initial", 0
        self.started, self.finished = False, False
        self.cpu_previous = None
        self.worker_modes, self.deadline, self.timed_out = [], None, False

    def poll(self):
        try:
            phase = (self.root / "pressure-phase").read_text()
        except OSError:
            return
        if phase == "constrained" and not self.started:
            self.started = True
            seconds = self.config["seconds"] // 3
            mode = self.config["pressure"]
            self.worker_modes = (
                ["cpu"] * len(os.sched_getaffinity(0)) if mode in ("cpu", "both") else []
            ) + (["gpu"] if mode in ("gpu", "both") else [])
            # The native worker separately bounds itself, including driver
            # initialization. Reap overdue children even if the client stalls.
            self.deadline = time.monotonic() + seconds + 6
            for index, worker_mode in enumerate(self.worker_modes):
                log = (self.root / f"pressure-{index}.log").open("w")
                self.logs.append(log)
                command = [str(self.root / "pressure"), worker_mode, str(seconds)]
                if worker_mode == "gpu":
                    command.append(self.config["render_node"])
                self.children.append(
                    subprocess.Popen(
                        command, env=self.environment, stdout=log, stderr=subprocess.STDOUT
                    )
                )
            self.events.append(
                dict(
                    event="started",
                    monotonic=time.monotonic(),
                    workers=len(self.children),
                    worker_modes=self.worker_modes,
                )
            )
        if self.started and not self.finished and not self.timed_out:
            if time.monotonic() > self.deadline and any(p.poll() is None for p in self.children):
                self.timed_out = True
                self.events.append(dict(event="timed-out", monotonic=time.monotonic()))
                for process in self.children:
                    if process.poll() is None:
                        process.kill()
        if self.started and not self.finished and all(p.poll() is not None for p in self.children):
            self.finished = True
            self.events.append(
                dict(
                    event="finished",
                    monotonic=time.monotonic(),
                    statuses=[p.returncode for p in self.children],
                )
            )
        self.phase = phase if phase in ("initial", "constrained", "recovery") else self.phase
        if time.monotonic() < self.next_sample:
            return
        self.next_sample = time.monotonic() + 0.25
        sample = dict(monotonic=time.monotonic(), phase=self.phase)
        selected = {f"cpu{cpu}" for cpu in os.sched_getaffinity(0)}
        ticks = [
            [int(v) for v in line.split()[1:9]]
            for line in Path("/proc/stat").read_text().splitlines()
            if line.split()[0] in selected
        ]
        total = sum(sum(row) for row in ticks)
        idle = sum(row[3] + row[4] for row in ticks)
        if self.cpu_previous and total > self.cpu_previous[0]:
            sample["affinity_cpu_busy_percent"] = 100 * (
                1 - (idle - self.cpu_previous[1]) / (total - self.cpu_previous[0])
            )
        self.cpu_previous = total, idle
        node = self.config.get("render_node")
        if node:
            device = Path("/sys/class/drm") / Path(node).name / "device"
            paths = [device / "gpu_busy_percent", *sorted(device.glob("hwmon/hwmon*/temp*_input"))]
            for path in paths:
                try:
                    sample[path.name] = int(path.read_text())
                except (OSError, ValueError):
                    pass
        self.samples.append(sample)

    def close(self):
        if self.started:
            self.poll()
        for process in self.children:
            if process.poll() is None:
                process.terminate()
        for process in self.children:
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for log in self.logs:
            log.close()
        completed = [
            "completed batches=" in (self.root / f"pressure-{i}.log").read_text()
            for i in range(len(self.children))
        ]
        result = dict(
            mode=self.config["pressure"],
            worker_modes=self.worker_modes,
            cpu_affinity=sorted(os.sched_getaffinity(0)),
            events=self.events,
            telemetry=self.samples,
            passed=bool(
                self.finished
                and not self.timed_out
                and bool(self.children)
                and len(self.children) == len(self.worker_modes)
                and all(p.returncode == 0 for p in self.children)
                and all(completed)
            ),
        )
        (self.root / "pressure.json").write_text(json.dumps(result, indent=2) + "\n")
