"""Fetch only Discord modules from its Linux distribution API, never the host runtime."""

from __future__ import annotations

import argparse
import concurrent.futures
import fcntl
import hashlib
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath

from support import HTTPSRedirect, checksum, download, object_value, read_object, write_json

REQUIRED = {"discord_desktop_core", "discord_voice", "discord_krisp", "discord_utils"}
PACKAGE_ROOT = Path(__file__).resolve().parents[2]
FAILURES = (
    OSError,
    ValueError,
    KeyError,
    TypeError,
    subprocess.SubprocessError,
    tarfile.TarError,
    http.client.HTTPException,
)


def compatibility_key() -> str:
    files = [
        "scripts/client_native.py",
        "scripts/common.py",
        "scripts/patch_app_audio.py",
        "scripts/patch_krisp.py",
        "scripts/camera_scope.py",
        "packaging/upstream/krisp-patcher.py",
        "integration/updater/client.py",
        "integration/updater/support.py",
        "integration/updater/patches.cjs",
    ]
    files += [
        str(path.relative_to(PACKAGE_ROOT))
        for path in sorted((PACKAGE_ROOT / "integration/audio").iterdir())
        if path.is_file()
    ]
    return identity({name: checksum(PACKAGE_ROOT / name) for name in files})


def identity(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def fetch(url: str, target: Path, expected: str, limit: int = 512 * 1024 * 1024):
    download(url, target, limit=limit)
    if checksum(target) != expected:
        raise ValueError("Discord module checksum mismatch")


def normalize(manifest: dict, channel: str) -> dict:
    manifest = object_value(manifest, "Discord manifest")
    host = object_value(manifest["full"], "Discord host")["host_version"]
    if (
        not isinstance(host, list)
        or len(host) != 3
        or any(type(x) is not int or x < 0 for x in host)
    ):
        raise ValueError("Invalid Discord host version")
    version = ".".join(map(str, host))
    modules = {}
    for name, item in object_value(manifest["modules"], "Discord modules").items():
        if not re.fullmatch(r"discord_[a-z0-9_]+", name):
            raise ValueError("Invalid Discord module name")
        item = object_value(object_value(item, name)["full"], name + " source")
        revision = item["module_version"]
        url = f"https://{channel}.dl2.discordapp.net/distro/app/{channel}/linux/x64/{version}/{name}/{revision}/full.distro"
        if (
            type(revision) is not int
            or revision < 0
            or item["host_version"] != host
            or item["url"] != url
            or not re.fullmatch(r"[a-f0-9]{64}", item["package_sha256"])
        ):
            raise ValueError(f"Invalid Discord module source: {name}")
        modules[name] = {"url": url, "sha256": item["package_sha256"], "version": revision}
    if not (REQUIRED | set(manifest.get("required_modules", []))).issubset(modules):
        raise ValueError("Discord manifest is missing required modules")
    # Discard full.url and host deltas. No code path can fetch the stock runtime.
    return {"channel": channel, "version": version, "modules": modules}


def latest(channel: str) -> dict:
    url = f"https://updates.discord.com/distributions/app/manifests/latest?channel={channel}&platform=linux&arch=x64"
    opener = urllib.request.build_opener(HTTPSRedirect())
    opener.addheaders = [("User-Agent", "Discord-Updater/1")]
    with opener.open(url, timeout=10) as response:
        data = response.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise ValueError("Discord manifest exceeds size limit")
    return normalize(json.loads(data), channel)


class ArchiveStream:
    """Bound all decompressed bytes, including tar metadata and trailing data."""

    def __init__(self, stream, limit: int):
        self.stream = stream
        self.remaining = limit

    def read(self, size: int):
        data = self.stream.read(min(size, self.remaining + 1))
        self.remaining -= len(data)
        if self.remaining < 0:
            raise ValueError("Discord module exceeds extracted size limit")
        return data


def extract_module(archive: Path, destination: Path, *, limit: int = 2 * 1024 * 1024 * 1024):
    """Extract files/ with internal relative symlinks; reject traversal and devices."""
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(
            ["brotli", "--decompress", "--stdout", str(archive)],
            stdout=subprocess.PIPE,
            stderr=errors,
        )
        total = 0
        stream = ArchiveStream(process.stdout, limit)
        try:
            with tarfile.open(fileobj=stream, mode="r|") as bundle:
                for count, member in enumerate(bundle, start=1):
                    if count > 100000:
                        raise ValueError("Too many Discord archive entries")
                    if member.name == "delta_manifest.json":
                        continue
                    name = PurePosixPath(member.name)
                    if name.parts == ("files",) and member.isdir():
                        continue
                    if (
                        len(name.parts) < 2
                        or name.parts[0] != "files"
                        or ".." in name.parts
                        or not (member.isfile() or member.isdir() or member.issym())
                    ):
                        raise ValueError(f"Unsafe Discord archive entry: {member.name}")
                    target = destination.joinpath(*name.parts[1:])
                    if (
                        not target.resolve().is_relative_to(destination.resolve())
                        or target.is_symlink()
                    ):
                        raise ValueError(f"Unsafe Discord archive path: {member.name}")
                    if member.issym():
                        if Path(member.linkname).is_absolute() or not (
                            target.parent / member.linkname
                        ).resolve().is_relative_to(destination.resolve()):
                            raise ValueError(f"Unsafe Discord archive link: {member.name}")
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.symlink_to(member.linkname)
                        continue
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    total += member.size
                    if total > limit:
                        raise ValueError("Discord module exceeds extracted size limit")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.extractfile(member) as source, target.open("xb") as output:
                        shutil.copyfileobj(source, output)
                    target.chmod(0o700 if member.mode & 0o111 else 0o600)
            while stream.read(1024 * 1024):
                pass
            if process.wait(timeout=60):
                raise ValueError("Unable to decompress Discord module")
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.kill()
                process.wait()


def module_path(cache: Path, name: str, source: dict, compatibility: str) -> Path:
    return cache / (name + "-" + source["sha256"] + "-" + compatibility)


def install_module(
    cache: Path, name: str, source: dict, compatibility: str, executable: Path
) -> Path:
    destination = module_path(cache, name, source, compatibility)
    if (destination / ".complete").is_file():
        return destination
    print("Downloading " + name, file=sys.stderr)
    with tempfile.TemporaryDirectory(prefix="module-", dir=cache) as temporary:
        archive = Path(temporary) / "module.br"
        fetch(source["url"], archive, source["sha256"])
        extracted = Path(temporary) / "files"
        extracted.mkdir()
        extract_module(archive, extracted)
        if not (extracted / "index.js").is_file():
            raise ValueError(f"Discord module has no entry point: {name}")
        if name == "discord_desktop_core":
            # Electron's Node mode reads ASARs without opening a window. Validate
            # our in-memory transforms before making this generation current.
            subprocess.run(
                [
                    str(executable),
                    str(PACKAGE_ROOT / "integration/updater/patches.cjs"),
                    str(extracted),
                ],
                env={
                    **os.environ,
                    "ELECTRON_RUN_AS_NODE": "1",
                    "LD_PRELOAD": "",
                    "NODE_OPTIONS": "",
                },
                check=True,
                timeout=30,
                stdout=sys.stderr,
            )
        if name in ("discord_voice", "discord_krisp"):
            subprocess.run(
                [
                    sys.executable,
                    str(PACKAGE_ROOT / "scripts/client_native.py"),
                    str(extracted),
                    name,
                ],
                check=True,
                timeout=120,
                stdout=sys.stderr,
            )
        (extracted / ".complete").write_text(source["sha256"])
        extracted.rename(destination)
    return destination


def validate_host(root: Path, channel: str, selected: str) -> Path:
    if not isinstance(selected, str) or not re.fullmatch(r"[a-f0-9]{64}", selected):
        raise ValueError("Invalid current Discord generation")
    host = root / "generations" / selected
    state = read_object(host / "client.json")
    if state["channel"] != channel or identity(state) != selected:
        raise ValueError("Discord generation identity mismatch")
    for name, source in object_value(state["modules"], "Cached Discord modules").items():
        module = module_path(root / "cache", name, source, state["compatibility"])
        if (
            (module / ".complete").read_text() != source["sha256"]
            or not (module / "index.js").is_file()
            or (host / "modules" / name).resolve() != module.resolve()
        ):
            raise ValueError(f"Incomplete Discord module: {name}")
    return host


def current_host(root: Path, channel: str) -> Path | None:
    pointer = root / "current.json"
    return (
        validate_host(root, channel, json.loads(pointer.read_text())) if pointer.exists() else None
    )


def ensure_client(
    root: Path, channel: str, *, offline: bool = False, executable: Path | None = None
) -> Path:
    if channel not in ("stable", "ptb", "canary"):
        raise ValueError("Unknown Discord channel")
    root = root.resolve()
    executable = executable or Path(
        os.environ.get("DPE_RUNTIME_EXECUTABLE", str(PACKAGE_ROOT / "runtime/electron"))
    )
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (root / ".update.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            previous = current_host(root, channel)
        except FAILURES as error:
            if offline:
                raise ValueError(
                    "Cached Discord client is incomplete; reconnect to repair it"
                ) from error
            print(
                f"Discord cache unavailable; checking for a complete client: {error}",
                file=sys.stderr,
            )
            previous = None
        if offline:
            if previous is None:
                raise ValueError("First launch requires an internet connection")
            return previous
        try:
            state = latest(channel)
            state["compatibility"] = compatibility_key()
            generation = identity(state)
            if previous and previous.name == generation:
                return previous
            if previous:
                old = read_object(previous / "client.json")
                if tuple(map(int, state["version"].split("."))) < tuple(
                    map(int, old["version"].split("."))
                ):
                    raise ValueError("Refusing a Discord host downgrade")
                if state["version"] == old["version"] and any(
                    source["version"] < old["modules"][name]["version"]
                    for name, source in state["modules"].items()
                    if name in old["modules"]
                ):
                    raise ValueError("Refusing a Discord module downgrade")
            cache = root / "cache"
            cache.mkdir(exist_ok=True)
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                list(
                    pool.map(
                        lambda item: install_module(
                            cache, *item, state["compatibility"], executable
                        ),
                        state["modules"].items(),
                    )
                )
            generations = root / "generations"
            generations.mkdir(exist_ok=True)
            destination = generations / generation
            if not destination.exists():
                with tempfile.TemporaryDirectory(
                    prefix="generation-", dir=generations
                ) as temporary:
                    staged = Path(temporary) / "client"
                    (staged / "modules").mkdir(parents=True)
                    for name, source in state["modules"].items():
                        (staged / "modules" / name).symlink_to(
                            module_path(cache, name, source, state["compatibility"])
                        )
                    (staged / "resources").mkdir()
                    write_json(
                        staged / "resources/build_info.json",
                        {
                            "releaseChannel": channel,
                            "version": state["version"],
                            "newUpdater": True,
                        },
                    )
                    write_json(staged / "client.json", state)
                    staged.rename(destination)
            validate_host(root, channel, generation)
            write_json(root / "current.json", generation)
            return destination
        except FAILURES as error:
            if previous is None:
                raise
            print(
                f"Discord update failed; using the previous complete client: {error}",
                file=sys.stderr,
            )
            return previous


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("channel", choices=("stable", "ptb", "canary"))
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    print(ensure_client(args.root.resolve(), args.channel, offline=args.offline))


if __name__ == "__main__":
    try:
        main()
    except FAILURES as error:
        print(f"Unable to prepare Discord: {error}", file=sys.stderr)
        raise SystemExit(1) from error
