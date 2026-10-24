"""Build the pinned reference fork or an upstream Electron channel candidate.

Artifacts remain diagnostic until independent runtime and release gates pass.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import threading
import time

from assemble import assemble, validate_port
from common import ROOT, digest, electron_channel, enabled_electron_channels, require_enabled_electron, hash_files, read_json, run, sha256, write_json
from build_resources import build_resources
from fingerprints import runtime_key
from compiler_cache import compiler_cache

IMPLEMENTATION = ["scripts/build_reference.py", "scripts/assemble.py", "scripts/common.py", "scripts/runtime_fingerprints.py",
                  "scripts/compiler_cache.py", "config/compiler-cache.json"]


def build_config(target: str) -> dict:
    if target != "reference":
        require_enabled_electron(target)
    config = read_json(ROOT / "config/build.json")
    if target != "reference":
        candidate = read_json(ROOT / "config/candidate-build.json")
        config = {**config, **candidate, "gn_args": config["gn_args"] + candidate["gn_args"]}
    return config


def install_toolchain(source: Path, config: dict):
    destination = source / "dpe_build/BUILD.gn"
    destination.parent.mkdir(parents=True, exist_ok=True)
    content = (ROOT / "ci/gn/BUILD.gn.in").read_text().replace("@CPU_FLAGS@", " ".join(config["cpu_flags"]))
    if not destination.exists() or destination.read_text() != content:
        destination.write_text(content)
    # Upstream's extra flags are appended after a target's flags, overriding
    # runtime-dispatched SIMD targets (e.g. Skia's x86-64-v4 implementation).
    # Derive a local template with baseline flags first; keep the upstream file
    # untouched and preserve its compiler, Rust linker and all other tool rules.
    template = baseline_toolchain((source / "build/toolchain/gcc_toolchain.gni").read_text())
    adapted = destination.with_name("gcc_toolchain.gni")
    if not adapted.exists() or adapted.read_text() != template:
        adapted.write_text(template)


def baseline_toolchain(template: str) -> str:
    replacements = [
        ("{{cflags}} {{cflags_c}}${extra_cppflags}", "${extra_cppflags} {{cflags}} {{cflags_c}}", 1),
        ("{{cflags}} {{cflags_cc}}${extra_cppflags}", "${extra_cppflags} {{cflags}} {{cflags_cc}}", 2),
        ("{{asmflags}}${extra_asmflags}", "${extra_asmflags} {{asmflags}}", 1),
    ]
    for old, new, count in replacements:
        if template.count(old) != count:
            raise ValueError("Upstream compiler template changed; review baseline flag ordering")
        template = template.replace(old, new)
    # Keep build arguments declared at their one canonical upstream location.
    # Only derive the templates, under private names so the imported originals
    # remain available to Chromium's host toolchains.
    start = 'template("single_gcc_toolchain")'
    if template.count(start) != 1:
        raise ValueError("Upstream compiler template changed; missing toolchain entry")
    template = template[template.index(start):]
    names = re.findall(r'template\("(\w+)"\)', template)
    template = re.sub(r'\b(' + '|'.join(map(re.escape, names)) + r')\b',
                      lambda match: 'dpe_' + match[0], template)
    return 'import("//build/toolchain/gcc_toolchain.gni")\n\n' + template


def enable_compiler_cache(target: str, args_file: Path) -> bool:
    # Inserting a wrapper changes every Ninja compile command. Preserve the
    # expensive reference run's pre-cache objects when resuming that workspace.
    return target != "reference" or not args_file.exists() or "cc_wrapper=" in args_file.read_text().replace(" ", "")


def cache_root() -> Path:
    return Path(os.environ.get("ELECTRON_BUILD_CACHE", str(ROOT / ".work/build-cache"))).resolve()


def reference_inputs(fork: dict, config: dict) -> dict:
    # This is a resumable workspace key, not the release/artifact identity.
    # Driver-only fixes must not discard a successfully fetched Chromium tree.
    return {"fork": fork, "config": config, "container": hash_files(["ci/runner/Dockerfile"])}


def build_inputs(target: str, sources: dict, config: dict) -> tuple[dict, dict, str]:
    fork = sources["fork"]
    imported = ROOT / fork["patch"]
    if sha256(imported) != fork["patch_sha256"]:
        raise ValueError("Discord patch differs from its locked checksum")
    inputs = reference_inputs(fork, config)
    if target == "reference":
        spec = {"commit": fork["commit"], "version": "44.0.0-reference.1", "repository": fork["repository"]}
        key = digest({"inputs": inputs, "implementation": hash_files(IMPLEMENTATION)})
    else:
        require_enabled_electron(target)
        spec = {**sources["targets"][target], "repository": sources["upstream_repository"]}
        if electron_channel(spec["version"]) != target:
            raise ValueError("Electron target version belongs to a different channel")
        port = sources.get("ports", {}).get(spec["version"].split(".")[0])
        if port:
            validate_port(port, imported)
            port = {key: value for key, value in port.items() if key != "status"}
        inputs = {**inputs, "target": {key: spec[key] for key in ("version", "commit")}, "port": port,
                  "candidate_toolchain": hash_files(["ci/gn/BUILD.gn.in", "config/candidate-build.json"])}
        key = runtime_key(spec, sources, config)
    return spec, inputs, key


def export_artifact(work: Path, output: Path, report: dict, reused: bool):
    name = "reference-dist.zip" if report["source"]["target"] == "reference" else "runtime-dist.zip"
    shutil.copy2(work / "checkout/src/out/Release/dist.zip", output / name)
    write_json(output / "source.json", report["source"])
    write_json(output / "capacity.json", {**report, "reused_completed_artifact": reused})


def reuse_completed(work: Path, output: Path, key: str) -> bool:
    marker = work / "completed.json"
    archive = work / "checkout/src/out/Release/dist.zip"
    if not marker.exists() or not archive.exists():
        return False
    report = read_json(marker)
    if (report.get("build_key") != key or sha256(archive) != report.get("archive_sha256")
            or not all(report.get("source", {}).get(name) is True for name in
                       ("source_assembled", "dependencies_patched", "compiled"))):
        return False
    export_artifact(work, output, report, reused=True)
    print("Reused verified completed runtime; no fetch, hooks, GN or compiler started", flush=True)
    return True


def patch_repositories(source: Path) -> dict:
    result = {}
    for entry in read_json(source / "src/electron/patches/config.json"):
        repo = (source / entry["repo"]).resolve()
        if not repo.is_relative_to(source.resolve()):
            raise ValueError("Dependency patch repository escapes the build workspace")
        if (repo / ".git").exists():
            result[entry["repo"]] = subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    return result


def reset_patch_repositories(source: Path, bases: dict):
    # Electron's patch hook uses git am and is not idempotent. Only reset these
    # generated, dedicated dependency checkouts to the recorded post-sync bases.
    for relative, commit in bases.items():
        repo = (source / relative).resolve()
        if not repo.is_relative_to(source.resolve()) or not (repo / ".git").exists():
            raise ValueError("Invalid cached dependency repository")
        am_state = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "--path-format=absolute", "--git-path", "rebase-apply"], text=True).strip()
        if Path(am_state).exists():
            run("git", "-C", repo, "am", "--abort")
        run("git", "-C", repo, "reset", "--hard", commit)


def verify_source(electron: Path, report: dict):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(electron), *args], text=True).strip()
    if git("rev-parse", "HEAD") != report["upstream_commit"] or git("write-tree") != report["tree"]:
        raise ValueError("Cached Electron source differs from its assembly report")
    if subprocess.run(["git", "-C", str(electron), "diff", "--quiet"]).returncode:
        raise ValueError("Cached Electron source contains unrecorded edits")


def build(output: Path, target: str = "reference", *, prepare_only: bool = False):
    started = time.monotonic()
    config = build_config(target)
    cache = cache_root()
    cache.mkdir(parents=True, exist_ok=True)
    sources = read_json(ROOT / "config/sources.lock.json")
    spec, inputs, key = build_inputs(target, sources, config)
    identity = digest(inputs)
    work = cache / target / identity
    work.mkdir(parents=True, exist_ok=True)
    if reuse_completed(work, output, key):
        return
    resources = build_resources()
    jobs = resources["parallel_jobs"]
    write_json(output / "resources.json", resources)
    print("Build scheduling: " + json.dumps(resources, sort_keys=True), flush=True)
    free = shutil.disk_usage(cache).free / 1_000_000_000
    memory = resources["memory_limit_bytes"]
    if free < config["minimum_disk_gb"] or memory / 1_000_000_000 < config["minimum_memory_gb"]:
        raise ValueError(f"Insufficient build capacity: {free:.1f} GB free, {memory / 1_000_000_000:.1f} GB RAM")
    write_json(work / "native-inputs.json", inputs)
    tools = cache / ("depot_tools-" + config["depot_tools_commit"])
    if not tools.exists():
        run("git", "init", tools)
    present = subprocess.run(["git", "-C", str(tools), "cat-file", "-e", config["depot_tools_commit"] + "^{commit}"], capture_output=True)
    if present.returncode:
        run("git", "-C", tools, "fetch", "--quiet", "--depth=1", "https://chromium.googlesource.com/chromium/tools/depot_tools.git", config["depot_tools_commit"])
    run("git", "-C", tools, "checkout", "--detach", config["depot_tools_commit"])
    os.environ["PATH"] = str(tools) + os.pathsep + os.environ["PATH"]
    os.environ["DEPOT_TOOLS_UPDATE"] = "0"
    # Disabling self-updates also skips gclient's normal Python bootstrap. GN's
    # launcher still requires python-bin/python3 and python3_bin_reldir.txt.
    # This upstream entry point installs tools without changing the pinned Git ref.
    run(tools / "ensure_bootstrap")
    source = work / "checkout"
    electron = source / "src/electron"
    report = work / "source.json"
    if not electron.exists():
        if not assemble(target, cache / "source.git", electron, report):
            shutil.copy2(report, output / "source.json")
            raise ValueError("Source port failed; compiler was not started")
    if (not report.exists() or read_json(report).get("upstream_commit") != spec["commit"]
            or read_json(report).get("source_assembled") is not True):
        raise ValueError("Incomplete or mismatched cached source assembly; inspect the cache")
    verify_source(electron, read_json(report))
    shutil.copy2(report, output / "source.json")
    (source / ".gclient").write_text("solutions = " + repr([{"name": "src/electron", "url": spec["repository"],
        "managed": False, "custom_deps": {}, "custom_vars": {}}]) + "\ntarget_os = ['linux']\n")
    prepared = work / "prepared.json"
    synced = work / "synchronized.json"
    if not synced.exists():
        run("gclient", "sync", "--nohooks", "--no-history", f"--jobs={resources['sync_jobs']}", cwd=source)
        write_json(work / "patch-bases.json", patch_repositories(source))
        write_json(synced, {"identity": identity})
    # unmanaged means gclient must preserve the exact source assembled above.
    verify_source(electron, read_json(report))
    if not prepared.exists():
        installed = work / "dependencies-installed.json"
        if not installed.exists():
            run("sudo", "bash", "build/install-build-deps.sh", "--no-prompt", cwd=source / "src")
            write_json(installed, {"identity": identity})
        reset_patch_repositories(source, read_json(work / "patch-bases.json"))
        run("gclient", "runhooks", cwd=source)
        write_json(prepared, {"identity": identity})
    if prepare_only:
        write_json(output / "preparation.json", {"target": target, "identity": identity,
            "workspace": str(work), "runtime_key": key, "source_assembled": True,
            "dependencies_patched": True, "compiled": False, "publishable": False})
        print("Source and dependencies prepared; no GN or Electron compiler started", flush=True)
        return
    args = config["gn_args"] + ['override_electron_version="' + spec["version"] + '"',
                                f"concurrent_links={resources['concurrent_links']}"]
    if target != "reference":
        install_toolchain(source / "src", config)
    cached = enable_compiler_cache(target, source / "src/out/Release/args.gn")
    context = compiler_cache(cache, source / "src", output) if cached else nullcontext(None)
    with context as wrapper:
        if wrapper:
            args += ["cc_wrapper=" + json.dumps(shlex.quote(str(wrapper)))]
        else:
            write_json(output / "compiler-cache.json", {"enabled": False,
                "reason": "Preserve existing unwrapped reference Ninja commands and objects"})
        run("gn", "gen", "out/Release", "--args=" + " ".join(args), cwd=source / "src")
        run("autoninja", "-C", "out/Release", "-j", str(jobs), "electron:electron_dist_zip", cwd=source / "src")
    archive = source / "src/out/Release/dist.zip"
    if not archive.is_file():
        raise ValueError("Electron distribution target did not produce dist.zip")
    source_report = read_json(report)
    source_report.update(dependencies_patched=True, compiled=True)
    write_json(report, source_report)
    shutil.copy2(report, output / "source.json")
    completed = {"source": source_report, "config": config, "identity": identity,
        "build_key": key, "runtime_key": key if target != "reference" else None,
        "compiled_version": spec["version"], "implementation": hash_files(IMPLEMENTATION),
        "compiler_cache_enabled": cached,
        "resources": resources,
        "parallel_jobs": jobs, "runner_name": os.environ.get("RUNNER_NAME"),
        "elapsed_seconds": round(time.monotonic() - started), "initial_free_disk_gb": free,
        "final_free_disk_gb": shutil.disk_usage(cache).free / 1_000_000_000,
        "memory_limit_bytes": memory, "archive_sha256": sha256(archive), "archive_bytes": archive.stat().st_size,
        "runtime_validated": False, "publishable": False}
    write_json(work / "completed.json", completed)
    export_artifact(work, output, completed, reused=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=["reference", *enabled_electron_channels()], default="reference")
    parser.add_argument("--output", type=Path, default=ROOT / "out/capacity")
    parser.add_argument("--prepare-only", action="store_true",
                        help="Fetch and patch a separate target workspace without starting its compiler")
    args = parser.parse_args()
    cache = cache_root()
    cache.mkdir(parents=True, exist_ok=True)
    output = args.output.resolve()
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
        build(output, args.target, prepare_only=args.prepare_only)
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
