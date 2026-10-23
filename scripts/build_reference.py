"""Cold build the pinned Discord fork to measure real CI capacity.

This produces a diagnostic reference artifact, never a latest-Electron release.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import threading
import time

from assemble import assemble
from common import ROOT, digest, hash_files, read_json, run, sha256, write_json


def cache_root() -> Path:
    return Path(os.environ.get("ELECTRON_BUILD_CACHE", str(ROOT / ".work/build-cache"))).resolve()


def memory_limit() -> int:
    physical = int(next(line.split()[1] for line in Path("/proc/meminfo").read_text().splitlines() if line.startswith("MemTotal:"))) * 1024
    cgroup = Path("/sys/fs/cgroup/memory.max")
    if cgroup.exists() and cgroup.read_text().strip() != "max":
        return min(physical, int(cgroup.read_text()))
    return physical


def build(output: Path):
    started = time.monotonic()
    config = read_json(ROOT / "config/build.json")
    cache = cache_root()
    cache.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(cache).free / 1_000_000_000
    memory = memory_limit()
    if free < config["minimum_disk_gb"] or memory / 1_000_000_000 < config["minimum_memory_gb"]:
        raise ValueError(f"Insufficient build capacity: {free:.1f} GB free, {memory / 1_000_000_000:.1f} GB RAM")
    fork = read_json(ROOT / "config/sources.lock.json")["fork"]
    identity = digest({"fork": fork, "config": config, "inputs": hash_files([
        "scripts/build_reference.py", "scripts/assemble.py", "scripts/common.py", "ci/runner/Dockerfile"])})
    work = cache / "reference" / identity
    work.mkdir(parents=True, exist_ok=True)
    tools = cache / ("depot_tools-" + config["depot_tools_commit"])
    if not tools.exists():
        run("git", "init", tools)
    run("git", "-C", tools, "fetch", "--quiet", "--depth=1", "https://chromium.googlesource.com/chromium/tools/depot_tools.git", config["depot_tools_commit"])
    run("git", "-C", tools, "checkout", "--detach", config["depot_tools_commit"])
    os.environ["PATH"] = str(tools) + os.pathsep + os.environ["PATH"]
    os.environ["DEPOT_TOOLS_UPDATE"] = "0"
    source = work / "checkout"
    electron = source / "src/electron"
    report = work / "source.json"
    if not electron.exists():
        assemble("reference", cache / "source.git", electron, report)
    if not report.exists() or read_json(report)["upstream_commit"] != fork["commit"]:
        raise ValueError("Incomplete or mismatched cached source assembly; inspect the cache")
    shutil.copy2(report, output / "source.json")
    (source / ".gclient").write_text("solutions = [{'name': 'src/electron', 'url': 'https://github.com/discord/electron.git', 'managed': False, 'custom_deps': {}, 'custom_vars': {}}]\ntarget_os = ['linux']\n")
    prepared = work / "prepared.json"
    if not prepared.exists():
        run("gclient", "sync", "--nohooks", "--no-history", "--jobs=4", cwd=source)
    # unmanaged means gclient must preserve the exact source assembled above.
    actual = subprocess.check_output(["git", "-C", str(electron), "rev-parse", "HEAD"], text=True).strip()
    if actual != read_json(report)["upstream_commit"]:
        raise ValueError("gclient changed the locked Electron source")
    if not prepared.exists():
        run("sudo", "bash", "build/install-build-deps.sh", "--no-prompt", cwd=source / "src")
        run("gclient", "runhooks", cwd=source)
        write_json(prepared, {"identity": identity})
    # The version explicitly identifies this as a non-release capacity artifact.
    args = config["gn_args"] + ['override_electron_version="44.0.0-reference.1"']
    run("gn", "gen", "out/Release", "--args=" + " ".join(args), cwd=source / "src")
    run("autoninja", "-C", "out/Release", "-j", str(config["parallel_jobs"]), "electron:electron_dist_zip", cwd=source / "src")
    archive = source / "src/out/Release/dist.zip"
    if not archive.is_file():
        raise ValueError("Electron distribution target did not produce dist.zip")
    shutil.copy2(archive, output / "reference-dist.zip")
    source_report = read_json(report)
    source_report.update(dependencies_patched=True, compiled=True)
    write_json(report, source_report)
    shutil.copy2(report, output / "source.json")
    write_json(output / "capacity.json", {"source": read_json(report), "config": config, "identity": identity,
        "elapsed_seconds": round(time.monotonic() - started), "initial_free_disk_gb": free,
        "final_free_disk_gb": shutil.disk_usage(cache).free / 1_000_000_000,
        "memory_limit_bytes": memory, "archive_sha256": sha256(archive), "archive_bytes": archive.stat().st_size,
        "runtime_validated": False, "publishable": False})


def main():
    cache = cache_root()
    cache.mkdir(parents=True, exist_ok=True)
    output = ROOT / "out/capacity"
    output.mkdir(parents=True, exist_ok=True)
    stopped = threading.Event()
    metrics = {"status": "running", "minimum_free_disk_bytes": shutil.disk_usage(cache).free,
               "minimum_available_memory_kb": None, "sample_interval_seconds": 5}
    started = time.monotonic()

    def sample():
        metrics["minimum_free_disk_bytes"] = min(metrics["minimum_free_disk_bytes"], shutil.disk_usage(cache).free)
        available = int(next(line.split()[1] for line in Path("/proc/meminfo").read_text().splitlines() if line.startswith("MemAvailable:")))
        previous = metrics["minimum_available_memory_kb"]
        metrics["minimum_available_memory_kb"] = available if previous is None else min(previous, available)
        metrics["elapsed_seconds"] = round(time.monotonic() - started)
        for name in ("memory.peak", "memory.swap.peak"):
            path = Path("/sys/fs/cgroup") / name
            if path.exists():
                metrics[name + "_bytes"] = int(path.read_text())
        write_json(output / "metrics.json", metrics)

    def monitor():
        while not stopped.wait(5):
            sample()

    sample()
    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    try:
        build(output)
    except BaseException:
        metrics["status"] = "failed"
        raise
    else:
        metrics["status"] = "completed"
    finally:
        stopped.set()
        thread.join()
        sample()


if __name__ == "__main__":
    main()
