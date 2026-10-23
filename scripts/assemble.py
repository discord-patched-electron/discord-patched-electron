"""Assemble a fresh Electron checkout; stop on any incompatible patch.

The patch-base target checks that our imported delta reconstructs Discord's tree.
The reference target uses Discord's exact tree for the first capacity build.
Other targets use upstream release commits and the imported patch series.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess

from common import ROOT, read_json, run, sha256, write_json


def git(cache: Path, *args) -> str:
    return subprocess.check_output(["git", "-C", str(cache), *args], text=True).strip()


def prepare_cache(cache: Path, lock: dict) -> None:
    if not cache.exists():
        run("git", "init", "--bare", cache)
    for remote, commits in (
        (lock["fork"]["repository"], [lock["fork"]["base"], lock["fork"]["commit"]]),
        (lock["upstream_repository"], [target["commit"] for target in lock["targets"].values()]),
    ):
        for commit in commits:
            exists = subprocess.run(["git", "-C", str(cache), "cat-file", "-e", commit + "^{commit}"], capture_output=True)
            if exists.returncode:
                run("git", "-C", cache, "fetch", "--quiet", "--no-tags", "--filter=blob:none", remote, commit)


def assemble(target: str, cache: Path, destination: Path, report: Path) -> bool:
    lock = read_json(ROOT / "config/sources.lock.json")
    fork = lock["fork"]
    patch = ROOT / fork["patch"]
    if sha256(patch) != fork["patch_sha256"]:
        raise ValueError("Discord patch differs from its locked checksum")
    if destination.exists():
        raise ValueError(f"Refusing to overwrite checkout {destination}; choose a fresh destination")
    prepare_cache(cache, lock)
    commit = fork["commit"] if target == "reference" else fork["base"] if target == "patch-base" else lock["targets"][target]["commit"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    run("git", "-C", cache, "worktree", "add", "--detach", destination, commit)
    result = {"target": target, "upstream_commit": commit, "fork_commit": fork["commit"],
              "patch_sha256": fork["patch_sha256"], "source_assembled": False,
              "dependencies_patched": False, "compiled": False, "runtime_validated": False}
    if target != "reference":
        process = subprocess.run(["git", "-C", str(destination), "apply", "--3way", "--index", "--whitespace=nowarn", str(patch)], capture_output=True, text=True)
        result["patch_log"] = process.stdout + process.stderr
        if process.returncode:
            result["reason"] = "Discord patch requires a compatibility port"
            write_json(report, result)
            return False
    tree = git(destination, "write-tree")
    if target == "patch-base" and tree != git(cache, "rev-parse", fork["commit"] + "^{tree}"):
        raise ValueError("Imported patch does not reconstruct the fork's source tree")
    # Test the actual linked-binding and registration wiring, not just folder presence.
    for name, marker in (
        ("discord/module.cc", "electron_renderer_discord"),
        ("shell/common/node_bindings.cc", "electron_renderer_discord"),
        ("lib/renderer/api/module-list.ts", "electron_renderer_discord"),
    ):
        if marker not in (destination / name).read_text():
            raise ValueError(f"Missing Discord binding wiring in {name}")
    result.update(source_assembled=True, tree=tree)
    write_json(report, result)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=["reference", "patch-base", "stable", "prerelease", "nightly"])
    parser.add_argument("--cache", type=Path, default=ROOT / ".work/source.git")
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    success = assemble(args.target, args.cache.resolve(), args.destination.resolve(), args.report)
    print("Source assembled; compile/runtime verification still required" if success else "Source port required; see report")
    raise SystemExit(0 if success else 1)


if __name__ == "__main__":
    main()
