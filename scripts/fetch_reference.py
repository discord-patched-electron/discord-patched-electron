"""Retrieve a successful compiler artifact for isolated validation, not release."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import zipfile

from common import ROOT, digest, read_json, run, sha256, write_json
from build_reference import build_config
from fingerprints import runtime_key


def validate_capacity(capacity: dict, candidate: bool):
    # A new candidate must not strand client-only updates on the previously
    # verified runtime. This exact report hash is an explicit reviewed pin.
    good = read_json(ROOT / 'config/update-policy.json')['last_good_runtime']
    if candidate and digest(capacity) == good.get('capacity_sha256'):
        return
    source = capacity["source"]
    sources = read_json(ROOT / "config/sources.lock.json")
    if not all(source.get(key) is True for key in ("source_assembled", "dependencies_patched", "compiled")):
        raise ValueError("Compiler report lacks a completed build")
    if not candidate:
        if source.get("target") != "reference" or source.get("upstream_commit") != sources["fork"]["commit"]:
            raise ValueError("Compiler report does not establish the current reference build")
    else:
        channel = source.get("target")
        if channel not in sources["targets"]:
            raise ValueError("Unknown candidate channel")
        spec = sources["targets"][channel]
        config = build_config(channel)
        expected = runtime_key(spec, sources, config)
        if (source.get("upstream_commit") != spec["commit"] or capacity.get("compiled_version") != spec["version"]
                or capacity.get("config") != config or capacity.get("runtime_key") != expected
                or capacity.get("build_key") != expected):
            raise ValueError("Candidate differs from current source, CPU or compiler configuration")


def extract(archive: Path, destination: Path, expected: str):
    if sha256(archive) != expected:
        raise ValueError("Reference archive checksum differs from compiler report")
    if destination.exists():
        raise ValueError("Use a fresh runtime extraction directory")
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        for entry in entries:
            name = PurePosixPath(entry.filename)
            mode = entry.external_attr >> 16
            if name.is_absolute() or ".." in name.parts or "\\" in entry.filename or stat.S_ISLNK(mode):
                raise ValueError("Unexpected archive path or symbolic link")
        source.extractall(destination)
        for entry in entries:
            mode = entry.external_attr >> 16
            if not entry.is_dir() and mode:
                (destination / entry.filename).chmod(mode & 0o777)
    if not (destination / "electron").is_file():
        raise ValueError("Reference archive lacks an Electron executable")


def fetch(run_id: str, output: Path):
    if not re.fullmatch(r"[1-9][0-9]*", run_id):
        raise ValueError("Expected a numeric GitHub Actions run ID")
    repository = os.environ.get("GH_REPO", "")
    if repository != "discord-patched-electron/discord-patched-electron":
        raise ValueError("Only this project's compiler artifacts are accepted")
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use an empty download directory")
    metadata = json.loads(subprocess.check_output(
        ["gh", "api", f"repos/{repository}/actions/runs/{run_id}"], text=True))
    if (metadata["conclusion"] != "success" or metadata["head_branch"] != "main"
            or metadata["head_repository"]["full_name"] != repository
            or metadata["path"] not in {".github/workflows/capacity.yml", ".github/workflows/blacksmith-capacity.yml", ".github/workflows/build.yml"}):
        raise ValueError("Expected a successful main-branch compiler run")
    output.mkdir(parents=True, exist_ok=True)
    attempt = metadata["run_attempt"]
    candidate = metadata["path"] == ".github/workflows/build.yml"
    report_name = {".github/workflows/capacity.yml": "capacity-report",
                   ".github/workflows/blacksmith-capacity.yml": "blacksmith-capacity",
                   ".github/workflows/build.yml": "candidate-capacity"}[metadata["path"]]
    good = read_json(ROOT / 'config/update-policy.json')['last_good_runtime']
    cached = run_id == good['run'] and metadata['head_sha'] == good['compiler_commit']
    if cached:
        # A checksum-pinned prerelease asset retains the tested runtime beyond
        # Actions artifact expiry. Client/AUR promotion is a separate gate.
        run('gh','release','download',good['cache_release'],'--repo',repository,
            '--pattern','capacity.json','--dir',output/'capacity')
    else:
        run("gh", "run", "download", run_id, "--repo", repository,
            "--name", f"{report_name}-{run_id}-{attempt}", "--dir", output / "capacity")
    capacity = read_json(output / "capacity/capacity.json")
    if cached and digest(capacity) != good['capacity_sha256']:
        raise ValueError('Retained compiler report differs from the verified pin')
    validate_capacity(capacity, candidate)
    artifact = "candidate-runtime" if candidate else "reference-runtime"
    if cached:
        run('gh','release','download',good['cache_release'],'--repo',repository,
            '--pattern','runtime-dist.zip','--dir',output/'binary')
    else:
        run("gh", "run", "download", run_id, "--repo", repository,
            "--name", f"{artifact}-{run_id}", "--dir", output / "binary")
    archive = output / "binary" / ("runtime-dist.zip" if candidate else "reference-dist.zip")
    extract(archive, output / "runtime", capacity["archive_sha256"])
    write_json(output / "artifact-provenance.json", {"compiler_run": metadata["html_url"],
        "compiler_commit": metadata["head_sha"], "compiler_attempt": attempt,
        "validation_commit": os.environ.get("GITHUB_SHA"), "capacity": capacity,
        "archive_sha256": sha256(archive), "publishable": False})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    fetch(args.run_id, args.output)


if __name__ == "__main__":
    main()
