"""Small shared primitives; no network access at import time."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
DISCORD_CHANNELS = ("stable", "ptb", "canary")
ELECTRON_CHANNELS = ("stable", "prerelease", "nightly")


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text())


def write_json(path: Path, data: object) -> None:
    """Atomic and byte-stable: unchanged inputs do not change a file's mtime."""
    encoded = (json.dumps(data, sort_keys=True, indent=2) + "\n").encode()
    if path.exists() and path.read_bytes() == encoded:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temp = Path(stream.name)
        stream.write(encoded)
    os.replace(temp, path)


def run(*args, cwd=None, **kwargs):
    return subprocess.run([str(arg) for arg in args], cwd=cwd, check=True, **kwargs)


def download(url: str, target: Path, expected: str | None = None) -> Path:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise ValueError("Only credential-free HTTPS downloads are supported")
    if expected is not None and not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise ValueError("Invalid SHA-256")
    if target.exists() and expected and sha256(target) == expected:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
        temp = Path(stream.name)
    try:
        run("curl", "--fail", "--silent", "--show-error", "--location", "--proto", "=https",
            "--proto-redir", "=https", "--retry", "2", "--connect-timeout", "30",
            "--max-time", "600", "--output", temp, url)
        if expected and sha256(temp) != expected:
            raise ValueError(f"Checksum mismatch: {url}")
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
    return target


def package_name(discord: str, electron: str) -> str:
    if discord not in DISCORD_CHANNELS or electron not in ELECTRON_CHANNELS:
        raise ValueError("Unknown channel")
    return f"discord-{discord}-{electron}-patched-electron-bin"


def version_key(version: str):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:-(alpha|beta|nightly)\.(\d+))?", version)
    if not match:
        raise ValueError(f"Unsupported Electron version: {version}")
    major, minor, patch, phase, serial = match.groups()
    return (int(major), int(minor), int(patch), {"nightly": 0, "alpha": 1, "beta": 2, None: 3}[phase], int(serial or 0))


def electron_channel(version: str) -> str:
    version_key(version)
    return "nightly" if "-nightly." in version else "prerelease" if "-" in version else "stable"


def arch_version(version: str) -> str:
    version_key(version)
    # Arch orders 45.0.0.beta.1 *after* 45.0.0, but 45.0.0beta.1 before it.
    return version.removeprefix("v").replace("-", "")


def hash_files(paths: list[str]) -> dict:
    result = {}
    for name in sorted(paths):
        path = ROOT / name
        if not path.is_file():
            raise ValueError(f"Missing build input: {name}")
        result[name] = sha256(path)
    return result
