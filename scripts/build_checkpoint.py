"""Bounded, exact-input Ninja workspace checkpoints for free hosted caches.

Includes source timestamps, Git metadata and tools, not just compiler objects.
Never relocate a checkpoint: Clang modules embed physical source paths.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import subprocess
import tarfile
import tempfile
import time
from decimal import Decimal
from pathlib import Path

from build_reference import build_config, build_inputs, cache_root
from common import ROOT, read_json, sha256, write_json
from incremental_workspace import workspace_id

MAX_BYTES = 20_000_000_000  # Bounded below Blacksmith's 25 GB free cache capacity.


def identity(target: str) -> tuple[str, str]:
    _, inputs, key = build_inputs(
        target, read_json(ROOT / "config/sources.lock.json"), build_config(target)
    )
    return workspace_id(target, inputs), key


def pack(
    cache: Path, destination: Path, target: str, workspace: str, key: str, limit: int = MAX_BYTES
) -> dict:
    cache = cache.resolve()
    work = cache / target / workspace
    if not (work / "source.json").is_file():
        raise ValueError("No assembled source to checkpoint")
    if not read_json(work / "source.json").get("source_assembled"):
        raise ValueError("Source assembly failed; refusing checkpoint")
    if work.is_symlink():
        raise ValueError("Checkpoint requires a physical workspace, not an identity alias")
    entries = [f"{target}/{workspace}"]
    entries += [
        p.name
        for p in cache.iterdir()
        if p.name == "source.git" or p.name == "tools" or p.name.startswith("depot_tools-")
    ]
    destination.mkdir(parents=True, exist_ok=True)
    temporary = destination / "workspace.tar.zst.partial"
    archive = destination / "workspace.tar.zst"
    size = 0
    with tempfile.TemporaryFile() as errors:
        tar = subprocess.Popen(
            ["tar", "--format=posix", "-C", str(cache), "-cf", "-", *entries],
            stdout=subprocess.PIPE,
            stderr=errors,
        )
        compressor = subprocess.Popen(
            ["zstd", "-T2", "-3", "-q", "-c"],
            stdin=tar.stdout,
            stdout=subprocess.PIPE,
            stderr=errors,
        )
        tar.stdout.close()
        try:
            with temporary.open("wb") as output:
                while chunk := compressor.stdout.read(1024 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise ValueError("Workspace exceeds free checkpoint size budget")
                    output.write(chunk)
            if compressor.wait() or tar.wait():
                errors.seek(0)
                raise ValueError(
                    "Checkpoint compression failed: " + errors.read(4096).decode(errors="replace")
                )
            os.replace(temporary, archive)
        finally:
            for process in (compressor, tar):
                if process.poll() is None:
                    process.kill()
                process.wait()
            compressor.stdout.close()
            temporary.unlink(missing_ok=True)
    report = {
        "schema": 1,
        "cache_root": str(cache),
        "target": target,
        "identity": workspace,
        "runtime_key": key,
        "bytes": size,
        "sha256": sha256(archive),
    }
    write_json(destination / "checkpoint.json", report)
    return report


def restore(
    cache: Path, source: Path, target: str, workspace: str, key: str, limit: int = MAX_BYTES
) -> bool:
    manifest = source / "checkpoint.json"
    if not manifest.exists():
        return False
    report = read_json(manifest)
    cache = cache.resolve()
    expected = {
        "schema": 1,
        "cache_root": str(cache),
        "target": target,
        "identity": workspace,
        "runtime_key": key,
    }
    if workspace.startswith("incremental-"):
        expected.pop("runtime_key")
    if any(report.get(name) != value for name, value in expected.items()):
        raise ValueError("Checkpoint identity or physical source path differs")
    archive = source / "workspace.tar.zst"
    if (
        not 0 < archive.stat().st_size <= limit
        or archive.stat().st_size != report["bytes"]
        or sha256(archive) != report["sha256"]
    ):
        raise ValueError("Checkpoint size or checksum differs")
    cache.mkdir(parents=True, exist_ok=True)
    if any(cache.iterdir()):
        raise ValueError(
            "Restore requires an empty workspace; refusing to overwrite build progress"
        )
    with subprocess.Popen(
        ["zstd", "-d", "-q", "-c", str(archive)], stdout=subprocess.PIPE
    ) as decoder:
        try:
            extract(decoder.stdout, cache, target, workspace)
            if decoder.wait():
                raise ValueError("Checkpoint decompression failed")
        finally:
            if decoder.poll() is None:
                decoder.kill()
    # Installed OS packages belong to the previous VM, unlike source hooks.
    (cache / target / workspace / "dependencies-installed.json").unlink(missing_ok=True)
    return True


def extract(stream, cache: Path, target: str, workspace: str):
    # Chromium's FFmpeg setup includes absolute links back into its own checkout.
    # Permit those only inside this exact cache root; retain their original text.
    def filter_member(member, destination):
        if member.issym() and Path(member.linkname).is_absolute():
            link = Path(member.linkname).resolve()
            if not link.is_relative_to(cache.resolve()):
                raise ValueError("Absolute checkpoint link escapes the build cache")
            relative = os.path.relpath(link, (cache / member.name).parent)
            checked = tarfile.data_filter(member.replace(linkname=relative), destination)
            return checked.replace(linkname=member.linkname)
        return tarfile.data_filter(member, destination)

    roots = {target, "source.git", "tools"}
    directories = []
    with tarfile.open(fileobj=stream, mode="r|") as bundle:
        for member in bundle:
            parts = Path(member.name).parts
            if not parts or (parts[0] not in roots and not parts[0].startswith("depot_tools-")):
                raise ValueError("Unexpected checkpoint root")
            if parts[0] == target and parts[:2] != (target, workspace):
                raise ValueError("Unexpected checkpoint workspace")
            bundle.extract(member, cache, filter=filter_member)
            if member.isfile() or member.isdir() or member.issym():
                timestamp = int(
                    Decimal(member.pax_headers.get("mtime", str(member.mtime))) * 1_000_000_000
                )
                if member.isdir():
                    directories.append((cache / member.name, timestamp))
                else:
                    os.utime(cache / member.name, ns=(timestamp, timestamp), follow_symlinks=False)
    # GN tracks directory mtimes for globbed inputs. Creating children dirties
    # their parents, so restore directories only after the entire extraction.
    for path, timestamp in reversed(directories):
        os.utime(path, ns=(timestamp, timestamp), follow_symlinks=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["pack", "restore", "identity"])
    parser.add_argument("--target", required=True)
    parser.add_argument("--directory", type=Path)
    args = parser.parse_args()
    workspace, key = identity(args.target)
    if args.action == "identity":
        print(workspace)
        return
    if args.directory is None:
        parser.error("--directory is required for pack/restore")
    started = time.monotonic()
    if args.action == "pack":
        with (cache_root() / ".build.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = pack(cache_root(), args.directory, args.target, workspace, key)
    else:
        result = restore(cache_root(), args.directory, args.target, workspace, key)
    print(result)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as stream:
            stream.write(f"Checkpoint {args.action}: {time.monotonic() - started:.1f} seconds.\n")
            if isinstance(result, dict):
                stream.write(f"Compressed workspace: {result['bytes'] / 1e9:.2f} GB (20 GB cap).\n")


if __name__ == "__main__":
    main()
