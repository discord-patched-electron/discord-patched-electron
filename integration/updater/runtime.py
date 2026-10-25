"""Update patched Electron from the project's approved, signed GitHub release."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import http.client
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath

from support import (
    checksum,
    download,
    object_value,
    progress,
    read_object,
    replace_damaged,
    write_json,
)

REPOSITORY = "discord-patched-electron/discord-patched-electron"
CACHE_REPOSITORY = "discord-patched-electron/cache"
CHECK_INTERVAL = 15 * 60
REQUIRED_FILES = ("electron", "icudtl.dat", "resources.pak", "LICENSE", "LICENSES.chromium.html")
FAILURES = (
    OSError,
    ValueError,
    KeyError,
    TypeError,
    subprocess.SubprocessError,
    zipfile.BadZipFile,
    http.client.HTTPException,
)


def electron_version(value: str) -> tuple:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:-(alpha|beta)\.(\d+))?", value)
    if not match:
        raise ValueError("Unsupported Electron version")
    major, minor, patch, track, revision = match.groups()
    return (
        int(major),
        int(minor),
        int(patch),
        {"alpha": 0, "beta": 1, None: 2}[track],
        int(revision or 0),
    )


def release_version(value: str) -> tuple:
    if not re.fullmatch(r"\d{8}\.\d{6}", value):
        raise ValueError("Invalid package release version")
    return tuple(map(int, value.split(".")))


def newer(candidate: dict, current: dict) -> bool:
    if candidate["archive_sha256"] == current["archive_sha256"]:
        return False
    incoming, installed = (
        electron_version(candidate["electron_version"]),
        electron_version(current["electron_version"]),
    )
    if incoming != installed:
        return incoming > installed
    # Rebuilds can fix Electron without changing its upstream version. Unreleased
    # local builds have no ordering information and retain their own runtime.
    return bool(current.get("release_version")) and release_version(
        candidate["release_version"]
    ) > release_version(current["release_version"])


def verify_signature(directory: Path, key: Path):
    with tempfile.TemporaryDirectory(prefix="runtime-key-") as temporary:
        keyring = Path(temporary) / "public.gpg"
        subprocess.run(
            [
                "gpg",
                "--batch",
                "--homedir",
                temporary,
                "--dearmor",
                "--output",
                str(keyring),
                str(key),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        subprocess.run(
            [
                "gpgv",
                "--homedir",
                temporary,
                "--keyring",
                str(keyring),
                str(directory / "SHA256SUMS.sig"),
                str(directory / "SHA256SUMS"),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
    checksums = {}
    for line in (directory / "SHA256SUMS").read_text().splitlines():
        match = re.fullmatch(r"([a-f0-9]{64})  ([A-Za-z0-9._+-]+)", line)
        if not match or match[2] in checksums:
            raise ValueError("Invalid signed checksum list")
        checksums[match[2]] = match[1]
    if checksum(directory / "release.json") != checksums.get("release.json"):
        raise ValueError("Release manifest checksum mismatch")


def approved_release(directory: Path, policy: dict, key: Path, *, fetch=False) -> dict:
    """Verify a signed release and the exact record written by manual approval.

    Retained copies can authorize offline use, but a signed prerelease alone or
    a release whose approval record names other bytes cannot authorize updates.
    """
    channel, track = policy["channel"], policy["electron_channel"]
    if channel not in ("canary", "ptb", "stable") or track not in ("prerelease", "stable"):
        raise ValueError("Invalid update channel")
    if fetch:
        download(
            f"https://api.github.com/repos/{REPOSITORY}/releases/latest", directory / "github.json"
        )
    release = read_object(directory / "github.json")
    tag = release["tag_name"]
    if (
        release.get("draft") is not False
        or release.get("prerelease") is not False
        or not re.fullmatch(r"[A-Za-z0-9._+-]+", tag)
    ):
        raise ValueError("Runtime updates require a published approved release")
    assets = {asset["name"]: asset["browser_download_url"] for asset in release["assets"]}
    for name in ("release.json", "SHA256SUMS", "SHA256SUMS.sig"):
        url = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{name}"
        if assets.get(name) != url:
            raise ValueError("Missing or unexpected signed release asset")
        if fetch:
            download(url, directory / name)
    verify_signature(directory, key)
    manifest = read_object(directory / "release.json")
    runtime = object_value(manifest["runtime"], "Release runtime")
    version = electron_version(runtime["version"])
    if (
        manifest.get("source_dirty") is not False
        or manifest.get("signing_required") is not True
        or manifest["channel"] != policy["channel"]
        or runtime["version"] != manifest["electron_version"]
        or ("prerelease" if version[3] < 2 else "stable") != policy["electron_channel"]
        or not re.fullmatch(r"[a-f0-9]{64}", runtime["archive_sha256"])
        or not re.fullmatch(r"[a-f0-9]{64}", runtime["runtime_key"])
        or runtime["cache_release"] != "runtime-cache-" + runtime["runtime_key"]
    ):
        raise ValueError("Release does not match this patched Electron installation")
    release_version(manifest["version"])
    approval_name = (
        "approved-release.json"
        if (channel, track) == ("canary", "prerelease")
        else f"approved-{channel}-{track}-release.json"
    )
    if fetch:
        download(
            f"https://raw.githubusercontent.com/{REPOSITORY}/main/config/{approval_name}",
            directory / "approval.json",
        )
    approval = read_object(directory / "approval.json")
    if (
        approval.get("schema") != 1
        or approval.get("tag") != tag
        or approval.get("version") != manifest["version"]
        or approval.get("manifest_sha256") != checksum(directory / "release.json")
        or not isinstance(approval.get("approved_by"), str)
        or not approval["approved_by"].strip()
    ):
        raise ValueError("Release has no matching manual approval")
    minimum = manifest["minimum_glibc"]
    if not re.fullmatch(r"\d+\.\d+", minimum):
        raise ValueError("Invalid minimum glibc version")
    local = os.confstr("CS_GNU_LIBC_VERSION").split()[-1]
    if tuple(map(int, local.split("."))) < tuple(map(int, minimum.split("."))):
        raise ValueError("New runtime requires a newer system glibc")
    return manifest


def latest(directory: Path, policy: dict, key: Path) -> dict:
    manifest = approved_release(directory, policy, key, fetch=True)
    runtime = manifest["runtime"]
    tag = read_object(directory / "github.json")["tag_name"]
    return {
        "schema": 1,
        "channel": policy["channel"],
        "electron_channel": policy["electron_channel"],
        "electron_version": runtime["version"],
        "runtime_key": runtime["runtime_key"],
        "archive_sha256": runtime["archive_sha256"],
        "release_version": manifest["version"],
        "tag": tag,
        "url": f"https://github.com/{CACHE_REPOSITORY}/releases/download/{runtime['cache_release']}/runtime-dist.zip",
    }


def extract(archive: Path, destination: Path):
    total = 0
    with zipfile.ZipFile(archive) as source:
        if len(source.infolist()) > 4096:
            raise ValueError("Too many runtime archive entries")
        seen = set()
        for member in source.infolist():
            name = PurePosixPath(member.filename)
            mode = member.external_attr >> 16
            if (
                not name.parts
                or name.is_absolute()
                or ".." in name.parts
                or "\\" in member.filename
                or member.filename in seen
                or stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)
            ):
                raise ValueError("Unsafe runtime archive entry")
            seen.add(member.filename)
            # Electron's upstream default_app.asar is unnecessary. The runtime
            # will use the project's existing bootstrap and mod loader instead.
            if name.parts[0] == "resources":
                continue
            if name.suffix in (".asar", ".node", ".distro", ".br") or name.parts[0] in (
                "modules",
                "core",
            ):
                raise ValueError("Runtime archive contains Discord client data")
            total += member.file_size
            if total > 2 * 1024 * 1024 * 1024:
                raise ValueError("Runtime exceeds extracted size limit")
            target = destination.joinpath(*name.parts)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(member) as data, target.open("xb") as output:
                    shutil.copyfileobj(data, output)
                target.chmod(0o700 if mode & 0o111 or member.filename == "electron" else 0o600)
    if not all((destination / name).is_file() for name in REQUIRED_FILES):
        raise ValueError("Incomplete patched Electron distribution")


def probe(executable: Path, version: str):
    result = subprocess.run(
        [str(executable), "-p", "process.versions.electron"],
        env={**os.environ, "ELECTRON_RUN_AS_NODE": "1", "LD_PRELOAD": "", "NODE_OPTIONS": ""},
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.stdout.strip() != version:
        raise ValueError("Downloaded Electron version differs from the signed release")


def runtime_files(directory: Path) -> dict:
    return {
        item.relative_to(directory).as_posix(): checksum(item)
        for item in sorted(directory.rglob("*"))
        if item.is_file()
        and item.name != "runtime.json"
        and item.relative_to(directory).parts[0] != "resources"
    }


def validate_generation(root: Path, app: Path, policy: dict, digest: str) -> tuple[Path, dict]:
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("Invalid runtime generation")
    generation = root / "generations" / digest
    info = read_object(generation / "runtime.json")
    if (
        info["archive_sha256"] != digest
        or info["channel"] != policy["channel"]
        or info["electron_channel"] != policy["electron_channel"]
        or not all((generation / name).is_file() for name in REQUIRED_FILES)
        or (generation / "resources").resolve() != (app / "resources").resolve()
        or checksum(generation / "electron") != info["executable_sha256"]
        or ("files" in info and runtime_files(generation) != info["files"])
    ):
        raise ValueError("Incomplete or mismatched runtime generation")
    return generation / "electron", info


def selected(root: Path, app: Path, policy: dict) -> tuple[Path, dict]:
    bundled = app / "runtime/electron"
    pointer = root / "current.json"
    if not pointer.exists():
        return bundled, policy
    executable, info = validate_generation(root, app, policy, json.loads(pointer.read_text()))
    if newer(info, policy):
        return executable, info
    return bundled, policy


def recently_checked(path: Path) -> bool:
    try:
        checked = json.loads(path.read_text())
        return type(checked) in (int, float) and 0 <= time.time() - checked < CHECK_INTERVAL
    except (OSError, ValueError):
        return False


def cache_root(profile: Path, app: Path) -> Path:
    # Different installations sharing a profile must not rewrite one another's
    # resource links, including a portable app relocated while another runs.
    identity = hashlib.sha256(str(app.resolve()).encode()).hexdigest()[:16]
    return profile / "dpe-runtime" / identity


def _ensure_runtime(app: Path, profile: Path, *, offline: bool) -> Path:
    app = app.resolve()
    profile = profile.resolve()
    bundled = app / "runtime/electron"
    policy_path = app / "runtime-update.json"
    if not policy_path.is_file() or os.environ.get("DPE_RUNTIME_BUNDLED") == "1":
        return bundled
    policy = read_object(policy_path)
    root = cache_root(profile, app)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / ".update.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            current, current_info = selected(root, app, policy)
        except FAILURES as error:
            print(
                f"Runtime cache unavailable; using the packaged runtime: {error}", file=sys.stderr
            )
            current, current_info = bundled, policy
        if offline or os.environ.get("DPE_RUNTIME_UPDATES") == "0":
            return current
        try:
            checked = root / "last-check.json"
            if recently_checked(checked):
                return current
            # Throttle failures as well as success; GitHub's anonymous API has a
            # shared IP rate limit. Checking again never delays module updates.
            write_json(checked, time.time())
            with tempfile.TemporaryDirectory(prefix="download-", dir=root) as temporary:
                directory = Path(temporary)
                incoming = latest(
                    directory, policy, app / "integration/updater/release-signing-key.asc"
                )
                if not newer(incoming, current_info):
                    return current
                generations = root / "generations"
                generations.mkdir(exist_ok=True)
                destination = generations / incoming["archive_sha256"]
                if destination.exists():
                    # A crash after rename but before publishing current.json
                    # leaves a complete generation. Verify and reuse it without
                    # downloading or mutating files a process might be using.
                    try:
                        executable, info = validate_generation(
                            root, app, policy, incoming["archive_sha256"]
                        )
                        if any(
                            info.get(key) != incoming[key]
                            for key in ("electron_version", "runtime_key", "archive_sha256")
                        ):
                            raise ValueError("Cached runtime differs from the signed release")
                        probe(executable, incoming["electron_version"])
                    except FAILURES as error:
                        print(f"Repairing damaged runtime cache: {error}", file=sys.stderr)
                    else:
                        write_json(root / "current.json", incoming["archive_sha256"])
                        return executable
                print(
                    f"Downloading patched Electron {incoming['electron_version']} from GitHub",
                    file=sys.stderr,
                )
                archive = directory / "runtime.zip"
                download(incoming["url"], archive, limit=512 * 1024 * 1024, label="Electron")
                progress("install", "Electron")
                if checksum(archive) != incoming["archive_sha256"]:
                    raise ValueError("Runtime archive checksum mismatch")
                staged = directory / "runtime"
                staged.mkdir()
                extract(archive, staged)
                (staged / "resources").symlink_to(app / "resources", target_is_directory=True)
                probe(staged / "electron", incoming["electron_version"])
                incoming["executable_sha256"] = checksum(staged / "electron")
                incoming["files"] = runtime_files(staged)
                write_json(staged / "runtime.json", incoming)
                replace_damaged(staged, destination)
                write_json(root / "current.json", incoming["archive_sha256"])
                progress("installed", "Electron")
                return destination / "electron"
        except FAILURES as error:
            print(
                f"Runtime update unavailable; keeping the current runtime: {error}", file=sys.stderr
            )
            return current


def ensure_runtime(app: Path, profile: Path, *, offline: bool = False) -> Path:
    try:
        return _ensure_runtime(app, profile, offline=offline)
    except FAILURES as error:
        # Cache creation and policy parsing are optional to launching the app.
        print(f"Runtime updater unavailable; using the packaged runtime: {error}", file=sys.stderr)
        return app.resolve() / "runtime/electron"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument("profile", type=Path)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    print(ensure_runtime(args.app, args.profile.resolve(), offline=args.offline))


if __name__ == "__main__":
    main()
