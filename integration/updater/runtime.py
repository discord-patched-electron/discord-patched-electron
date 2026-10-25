"""Update patched Electron from the project's approved, signed GitHub release."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

from client import HTTPSRedirect, checksum, write_json

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
)


def download(url: str, target: Path, *, limit: int = 4 * 1024 * 1024):
    if not url.startswith("https://"):
        raise ValueError("Runtime downloads must use HTTPS")
    opener = urllib.request.build_opener(HTTPSRedirect())
    opener.addheaders = [("User-Agent", "discord-patched-electron-updater/1")]
    with opener.open(url, timeout=20) as response, target.open("wb") as output:
        total = 0
        while block := response.read(1024 * 1024):
            total += len(block)
            if total > limit:
                raise ValueError("Runtime download exceeds size limit")
            output.write(block)


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


def latest(directory: Path, policy: dict, key: Path) -> dict:
    download(
        f"https://api.github.com/repos/{REPOSITORY}/releases/latest", directory / "github.json"
    )
    release = json.loads((directory / "github.json").read_text())
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
        download(url, directory / name)
    verify_signature(directory, key)
    manifest = json.loads((directory / "release.json").read_text())
    runtime = manifest["runtime"]
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
    minimum = manifest["minimum_glibc"]
    if not re.fullmatch(r"\d+\.\d+", minimum):
        raise ValueError("Invalid minimum glibc version")
    local = os.confstr("CS_GNU_LIBC_VERSION").split()[-1]
    if tuple(map(int, local.split("."))) < tuple(map(int, minimum.split("."))):
        raise ValueError("New runtime requires a newer system glibc")
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


def selected(root: Path, app: Path, policy: dict) -> tuple[Path, dict]:
    bundled = app / "runtime/electron"
    pointer = root / "current.json"
    if not pointer.exists():
        return bundled, policy
    digest = json.loads(pointer.read_text())
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("Invalid runtime generation")
    generation = root / "generations" / digest
    info = json.loads((generation / "runtime.json").read_text())
    if (
        info["archive_sha256"] != digest
        or info["channel"] != policy["channel"]
        or info["electron_channel"] != policy["electron_channel"]
        or (generation / "resources").resolve() != (app / "resources").resolve()
        or checksum(generation / "electron") != info["executable_sha256"]
    ):
        raise ValueError("Incomplete or mismatched runtime generation")
    if newer(info, policy):
        return generation / "electron", info
    return bundled, policy


def cache_root(profile: Path, app: Path) -> Path:
    # Different installations sharing a profile must not rewrite one another's
    # resource links, including a portable app relocated while another runs.
    identity = hashlib.sha256(str(app.resolve()).encode()).hexdigest()[:16]
    return profile / "dpe-runtime" / identity


def ensure_runtime(app: Path, profile: Path, *, offline: bool = False) -> Path:
    app = app.resolve()
    profile = profile.resolve()
    bundled = app / "runtime/electron"
    policy_path = app / "runtime-update.json"
    if not policy_path.is_file() or os.environ.get("DPE_RUNTIME_BUNDLED") == "1":
        return bundled
    policy = json.loads(policy_path.read_text())
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
            if (
                checked.exists()
                and 0 <= time.time() - json.loads(checked.read_text()) < CHECK_INTERVAL
            ):
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
                print(
                    f"Downloading patched Electron {incoming['electron_version']} from GitHub",
                    file=sys.stderr,
                )
                archive = directory / "runtime.zip"
                download(incoming["url"], archive, limit=512 * 1024 * 1024)
                if checksum(archive) != incoming["archive_sha256"]:
                    raise ValueError("Runtime archive checksum mismatch")
                staged = directory / "runtime"
                staged.mkdir()
                extract(archive, staged)
                (staged / "resources").symlink_to(app / "resources", target_is_directory=True)
                probe(staged / "electron", incoming["electron_version"])
                incoming["executable_sha256"] = checksum(staged / "electron")
                write_json(staged / "runtime.json", incoming)
                generations = root / "generations"
                generations.mkdir(exist_ok=True)
                destination = generations / incoming["archive_sha256"]
                if destination.exists():
                    # Keep running generations immutable. An invalid existing
                    # generation needs explicit cleanup, not an in-place rewrite.
                    raise ValueError("Runtime generation already exists but was not usable")
                staged.rename(destination)
                write_json(root / "current.json", incoming["archive_sha256"])
                return destination / "electron"
        except FAILURES as error:
            print(
                f"Runtime update unavailable; keeping the current runtime: {error}", file=sys.stderr
            )
            return current


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument("profile", type=Path)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    print(ensure_runtime(args.app, args.profile.resolve(), offline=args.offline))


if __name__ == "__main__":
    main()
