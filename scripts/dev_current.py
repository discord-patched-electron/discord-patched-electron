"""Resolve the current checkout to a cached, private development payload."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from common import ROOT, digest, read_json, sha256, write_json


def checkout():
    from dev import editable

    branch = (
        subprocess.check_output(
            ["git", "symbolic-ref", "--quiet", "--short", "HEAD"], cwd=ROOT, text=True
        ).strip()
        if subprocess.run(
            ["git", "symbolic-ref", "--quiet", "HEAD"], cwd=ROOT, capture_output=True
        ).returncode
        == 0
        else "detached"
    )
    names = (
        subprocess.check_output(
            [
                "git",
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
                "--",
                "integration",
                "native",
                "patches",
                "scripts",
                "config",
                "ci",
                "packaging",
                "Cargo.toml",
                "Cargo.lock",
                "rust-toolchain.toml",
                ".cargo/config.toml",
                "package.json",
                "bun.lock",
                "pyproject.toml",
                "uv.lock",
                "LICENSE",
                "THIRD_PARTY.md",
            ],
            cwd=ROOT,
        )
        .decode()
        .split("\0")
    )
    frozen, helpers = {}, {}
    for relative in sorted(set(names) - {""}):
        path = ROOT / relative
        if path.suffix == ".pyc" or "__pycache__" in path.parts:
            continue
        value = (
            {"sha256": sha256(path), "executable": bool(path.stat().st_mode & 0o111)}
            if path.is_file()
            else None
        )
        (helpers if editable(path) else frozen)[relative] = value
    return {
        "schema": 1,
        "checkout": str(ROOT.resolve()),
        "branch": branch,
        "build": digest(
            {
                "frozen": frozen,
                "helper_inventory": sorted(k for k, v in helpers.items() if v is not None),
            }
        ),
        "helpers": digest(helpers),
    }


def artifacts(root):
    # Never traverse compiler trees, profiles or runtime copies to find metadata.
    excluded = {
        ".git",
        "node_modules",
        "profiles",
        "objects",
        "runtime",
        "src",
        "source",
        "rust-target",
        "modules",
    }
    for directory, dirs, names in os.walk(root, followlinks=False):
        path = Path(directory)
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in excluded
            and not (path / d).is_symlink()
            and len(path.relative_to(root).parts) < 3
        )
        if "artifact-provenance.json" in names:
            yield path


def verified_artifact(path):
    from build_reference import build_config
    from fetch_reference import reviewed_compatibility, validate_provenance
    from runtime_fingerprints import runtime_key

    provenance = read_json(path / "artifact-provenance.json")
    capacity = provenance["capacity"]
    target = capacity["source"]["target"]
    sources = read_json(ROOT / "config/sources.lock.json")
    if target != "prerelease":
        raise ValueError("current needs a compiled prerelease runtime")
    expected = runtime_key(sources["targets"][target], sources, build_config(target))
    validate_provenance(provenance, True)
    run = str(provenance.get("compiler_run", "")).rsplit("/", 1)[-1]
    if capacity.get("runtime_key") != expected and not reviewed_compatibility(
        capacity, expected, run, provenance.get("compiler_commit")
    ):
        raise ValueError("Runtime does not match this checkout's native inputs")
    if sha256(path / "binary/runtime-dist.zip") != capacity["archive_sha256"]:
        raise ValueError("Runtime archive checksum mismatch")
    return capacity["runtime_key"]


def resolve(manager, artifact=None):
    from dev import lock

    with lock(manager.home / "locks/current-resolution", wait=True):
        return prepare(manager, artifact)


def prepare(manager, artifact=None):
    from dev import lock

    state = checkout()
    candidates = [Path(artifact).resolve()] if artifact else artifacts(ROOT / "out")
    selected = None
    for candidate in candidates:
        try:
            runtime = verified_artifact(candidate)
        except (OSError, ValueError, KeyError):
            if artifact:
                raise
            continue
        selected = candidate
        break
    if selected is None:
        raise ValueError(
            "No verified Electron artifact matches this checkout. Retain/fetch a matching "
            "compiler artifact under out/, or pass run current --artifact PATH. "
            "Electron source changes require a compiled artifact; no hosted build was started."
        )
    label = (
        "tree-"
        + digest({k: state[k] for k in ("checkout", "branch", "build")} | {"runtime": runtime})[:32]
    )
    app = manager.path("versions", label)
    record = app / "current-checkout.json"
    if not app.exists():
        print(f"Assembling current checkout ({state['branch']}) as {label}…", flush=True)
        manager.create(label, selected, ROOT / "config/clients.lock.json")
        if checkout() != state:
            manager.remove("versions", label)
            raise ValueError("Checkout changed during assembly; run current again")
    with lock(manager.home / "locks/catalog", wait=True):
        previous = read_json(record) if record.is_file() else None
        if previous != state:
            # Never replace helper bytes beneath an already-running profile.
            with lock(manager.home / "locks" / (label + ".version")):
                record.unlink(missing_ok=True)
                manager.sync(app)
                if checkout() != state:
                    raise ValueError("Checkout changed during preparation; run current again")
                write_json(record, state)
    manager.current_state = state
    print(f"Using current checkout ({state['branch']}): {label}", flush=True)
    return label
