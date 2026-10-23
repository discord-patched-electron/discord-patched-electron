"""Scheduling policy, separate from native input keys and incremental workspaces."""
from __future__ import annotations

import math
import os
from pathlib import Path

GIB = 1024 ** 3


def cgroup_directories(root: Path, membership: Path) -> list[Path]:
    """Include visible ancestors, both on a host and in a cgroup namespace."""
    directories = [root]
    if membership.exists():
        for line in membership.read_text().splitlines():
            if line.startswith("0::"):
                relative = Path(line[3:].lstrip("/"))
                if ".." not in relative.parts:
                    current = root / relative
                    while current != root:
                        directories.append(current)
                        current = current.parent
    return directories


def limits(cgroup: Path = Path("/sys/fs/cgroup"), membership: Path = Path("/proc/self/cgroup"),
           meminfo: Path = Path("/proc/meminfo")) -> tuple[float, int]:
    cpus = float(len(os.sched_getaffinity(0)))
    memory = int(next(line.split()[1] for line in meminfo.read_text().splitlines()
                      if line.startswith("MemTotal:"))) * 1024
    for directory in cgroup_directories(cgroup, membership):
        cpu_file = directory / "cpu.max"
        if cpu_file.exists():
            quota, period = cpu_file.read_text().split()
            if quota != "max":
                cpus = min(cpus, int(quota) / int(period))
        memory_file = directory / "memory.max"
        if memory_file.exists() and memory_file.read_text().strip() != "max":
            memory = min(memory, int(memory_file.read_text()))
    return cpus, memory


def select(cpus: float, memory: int, requested: str = "auto") -> dict:
    # Conservative initial budget: 2 GiB per task, plus 2 GiB for the runner,
    # cache server and other tools. This is a heuristic, not an OOM guarantee;
    # Chromium's unusually large individual translation units can exceed it.
    automatic = max(1, min(32, math.floor(cpus), (memory - 2 * GIB) // (2 * GIB)))
    if requested == "auto":
        jobs = automatic
    else:
        try:
            jobs = int(requested)
        except ValueError as error:
            raise ValueError("ELECTRON_BUILD_JOBS must be auto or an integer from 1 to 32") from error
        if not 1 <= jobs <= 32:
            raise ValueError("ELECTRON_BUILD_JOBS must be auto or an integer from 1 to 32")
    return {"parallel_jobs": jobs, "automatic_jobs": automatic, "requested_jobs": requested,
            "cpu_capacity": cpus, "memory_limit_bytes": memory,
            "sync_jobs": min(8, jobs), "concurrent_links": 1}


def build_resources() -> dict:
    return select(*limits(), os.environ.get("ELECTRON_BUILD_JOBS", "auto"))
