"""Retrieve a successful compiler artifact for isolated validation, not release."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import zipfile
from pathlib import Path, PurePosixPath

from build_reference import build_config
from common import ROOT, digest, read_json, run, sha256, write_json
from fingerprints import runtime_key

CACHE_REPOSITORY = "discord-patched-electron/cache"


def reviewed_compatibility(
    capacity: dict, expected: str, run_id: str | None, compiler_commit: str | None
) -> bool:
    """An exact reviewed report may cross a scheduling-only driver change."""
    policy = read_json(ROOT / "config/runtime-compatibility.json")
    if policy.get("schema") != 1:
        raise ValueError("Unsupported runtime compatibility policy")
    return any(
        entry["expected_runtime_key"] == expected
        and entry["run"] == run_id
        and entry["compiler_commit"] == compiler_commit
        and entry["capacity_sha256"] == digest(capacity)
        and entry["runtime_key"] == capacity.get("runtime_key") == capacity.get("build_key")
        for entry in policy["reviewed_reports"]
    )


def validate_capacity(
    capacity: dict,
    candidate: bool,
    *,
    run_id: str | None = None,
    compiler_commit: str | None = None,
):
    # A new candidate must not strand client-only updates on the previously
    # verified runtime. This exact report hash is an explicit reviewed pin.
    good = read_json(ROOT / "config/update-policy.json")["last_good_runtime"]
    if candidate and digest(capacity) == good.get("capacity_sha256"):
        return
    source = capacity["source"]
    sources = read_json(ROOT / "config/sources.lock.json")
    if not all(
        source.get(key) is True for key in ("source_assembled", "dependencies_patched", "compiled")
    ):
        raise ValueError("Compiler report lacks a completed build")
    if not candidate:
        if (
            source.get("target") != "reference"
            or source.get("upstream_commit") != sources["fork"]["commit"]
        ):
            raise ValueError("Compiler report does not establish the current reference build")
    else:
        channel = source.get("target")
        if channel not in sources["targets"]:
            raise ValueError("Unknown candidate channel")
        spec = sources["targets"][channel]
        config = build_config(channel)
        expected = runtime_key(spec, sources, config)
        if (
            source.get("upstream_commit") != spec["commit"]
            or capacity.get("compiled_version") != spec["version"]
            or capacity.get("config") != config
        ):
            raise ValueError("Candidate differs from current source, CPU or compiler configuration")
        if (
            capacity.get("runtime_key") != expected or capacity.get("build_key") != expected
        ) and not reviewed_compatibility(capacity, expected, run_id, compiler_commit):
            raise ValueError("Candidate differs from current source, CPU or compiler configuration")


def validate_provenance(provenance: dict, candidate: bool | None = None):
    """Preserve compiler identity when downstream checks revalidate an artifact."""
    capacity = provenance["capacity"]
    match = re.fullmatch(
        r"https://github\.com/discord-patched-electron/discord-patched-electron/actions/runs/([1-9][0-9]*)",
        str(provenance.get("compiler_run", "")),
    )
    validate_capacity(
        capacity,
        capacity["source"]["target"] != "reference" if candidate is None else candidate,
        run_id=match[1] if match else None,
        compiler_commit=provenance.get("compiler_commit"),
    )


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
            if (
                name.is_absolute()
                or ".." in name.parts
                or "\\" in entry.filename
                or stat.S_ISLNK(mode)
            ):
                raise ValueError("Unexpected archive path or symbolic link")
        source.extractall(destination)
        for entry in entries:
            mode = entry.external_attr >> 16
            if not entry.is_dir() and mode:
                (destination / entry.filename).chmod(mode & 0o777)
    if not (destination / "electron").is_file():
        raise ValueError("Reference archive lacks an Electron executable")


def fetch(run_id: str, output: Path, *, capacity_only=False, allow_development=False):
    if not re.fullmatch(r"[1-9][0-9]*", run_id):
        raise ValueError("Expected a numeric GitHub Actions run ID")
    repository = os.environ.get("GH_REPO", "")
    if repository != "discord-patched-electron/discord-patched-electron":
        raise ValueError("Only this project's compiler artifacts are accepted")
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use an empty download directory")
    metadata = json.loads(
        subprocess.check_output(
            ["gh", "api", f"repos/{repository}/actions/runs/{run_id}"], text=True
        )
    )
    if (
        metadata["conclusion"] != "success"
        or not (
            metadata["head_branch"] == "main"
            or (
                allow_development
                and metadata["head_branch"] == "dev"
                and metadata["path"] == ".github/workflows/build.yml"
                and metadata.get("event") == "workflow_dispatch"
            )
        )
        or metadata["head_repository"]["full_name"] != repository
        or metadata["path"]
        not in {
            ".github/workflows/capacity.yml",
            ".github/workflows/blacksmith-capacity.yml",
            ".github/workflows/build.yml",
            ".github/workflows/import-runtime.yml",
        }
    ):
        raise ValueError("Expected a successful main-branch compiler run")
    output.mkdir(parents=True, exist_ok=True)
    attempt = metadata["run_attempt"]
    imported = metadata["path"] == ".github/workflows/import-runtime.yml"
    candidate = metadata["path"] in {
        ".github/workflows/build.yml",
        ".github/workflows/import-runtime.yml",
    }
    report_name = {
        ".github/workflows/capacity.yml": "capacity-report",
        ".github/workflows/blacksmith-capacity.yml": "blacksmith-capacity",
        ".github/workflows/build.yml": "candidate-capacity",
        ".github/workflows/import-runtime.yml": "candidate-capacity",
    }[metadata["path"]]
    good = read_json(ROOT / "config/update-policy.json")["last_good_runtime"]
    cached = run_id == good["run"] and metadata["head_sha"] == good["compiler_commit"]
    if cached:
        # A checksum-pinned prerelease asset retains the tested runtime beyond
        # Actions artifact expiry. Client/AUR promotion is a separate gate.
        run(
            "gh",
            "release",
            "download",
            good["cache_release"],
            "--repo",
            CACHE_REPOSITORY,
            "--pattern",
            "capacity.json",
            "--dir",
            output / "capacity",
        )
    else:
        run(
            "gh",
            "run",
            "download",
            run_id,
            "--repo",
            repository,
            "--name",
            f"{report_name}-{run_id}-{attempt}",
            "--dir",
            output / "capacity",
        )
    capacity = read_json(output / "capacity/capacity.json")
    if cached and digest(capacity) != good["capacity_sha256"]:
        raise ValueError("Retained compiler report differs from the verified pin")
    validate_capacity(capacity, candidate, run_id=run_id, compiler_commit=metadata.get("head_sha"))
    if capacity_only:
        return capacity
    if cached and imported:
        run(
            "gh",
            "release",
            "download",
            good["cache_release"],
            "--repo",
            CACHE_REPOSITORY,
            "--pattern",
            "local-build-provenance.json",
            "--dir",
            output / "capacity",
        )
        cache = json.loads(
            subprocess.check_output(
                [
                    "gh",
                    "api",
                    f"repos/{CACHE_REPOSITORY}/releases/tags/{good['cache_release']}",
                ],
                text=True,
            )
        )
        provenance = output / "capacity/local-build-provenance.json"
        uploaded = {asset["name"]: asset.get("digest") for asset in cache["assets"]}
        if uploaded.get(provenance.name) != "sha256:" + sha256(provenance):
            raise ValueError("Retained local compiler provenance checksum mismatch")
    artifact = "candidate-runtime" if candidate else "reference-runtime"
    if cached:
        run(
            "gh",
            "release",
            "download",
            good["cache_release"],
            "--repo",
            CACHE_REPOSITORY,
            "--pattern",
            "runtime-dist.zip",
            "--dir",
            output / "binary",
        )
    else:
        run(
            "gh",
            "run",
            "download",
            run_id,
            "--repo",
            repository,
            "--name",
            f"{artifact}-{run_id}",
            "--dir",
            output / "binary",
        )
    archive = output / "binary" / ("runtime-dist.zip" if candidate else "reference-dist.zip")
    extract(archive, output / "runtime", capacity["archive_sha256"])
    write_json(
        output / "artifact-provenance.json",
        {
            "compiler_run": metadata["html_url"],
            "compiler_commit": metadata["head_sha"],
            "compiler_attempt": attempt,
            "validation_commit": os.environ.get("GITHUB_SHA"),
            "capacity": capacity,
            **(
                {
                    "compiler_provider": "local",
                    "local_build_provenance": read_json(
                        output / "capacity/local-build-provenance.json"
                    ),
                }
                if imported
                else {}
            ),
            "archive_sha256": sha256(archive),
            "publishable": False,
        },
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--allow-development",
        action="store_true",
        help="Allow a successful manually dispatched dev build for local tests only",
    )
    args = parser.parse_args()
    fetch(args.run_id, args.output, allow_development=args.allow_development)


if __name__ == "__main__":
    main()
