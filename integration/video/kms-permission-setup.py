#!/usr/bin/python3 -I
# SPDX-License-Identifier: MIT
"""Install/revoke one user's DPE capture permission after explicit polkit auth."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import pwd
import re
import stat
import sys
import tempfile
from pathlib import Path

ROOT = Path("/usr/local/libexec/discord-patched-electron")
SERVER = ROOT / "kms-server"
RULES = Path("/etc/polkit-1/rules.d")
ACTIONS = Path("/usr/share/polkit-1/actions")
ACTION = "org.discord-patched-electron.capture"
BUNDLE = Path(__file__).resolve().parents[2] / "capture"
MARKER = b"DPE-KMS-2\n"
POLICY = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE policyconfig PUBLIC "-//freedesktop//DTD PolicyKit Policy Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/PolicyKit/1/policyconfig.dtd">
<policyconfig>
  <vendor>Discord Patched Electron</vendor>
  <action id="{ACTION}">
    <description>Capture a monitor with Discord Patched Electron</description>
    <message>Enable direct screen capture in Discord Patched Electron settings.</message>
    <defaults>
      <allow_any>no</allow_any>
      <allow_inactive>no</allow_inactive>
      <allow_active>no</allow_active>
    </defaults>
    <annotate key="org.freedesktop.policykit.exec.path">{SERVER}</annotate>
  </action>
</policyconfig>
'''


def root_directory(path: Path):
    # Check every ancestor even when the leaf already exists. No symlink or
    # user-writable privileged installation directory is accepted/repaired.
    if path.parent != path:
        root_directory(path.parent)
    if not path.exists():
        path.mkdir(mode=0o755)
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != 0
        or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    ):
        raise ValueError("Untrusted permission directory")


def atomic_write(path: Path, data: bytes, mode: int):
    fd, temporary = tempfile.mkstemp(prefix=".dpe-kms-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as target:
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
            os.fchmod(target.fileno(), mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def rule(username: str) -> str:
    # The action's exec.path annotation binds pkexec to the fixed binary.
    # Status can query this action without forbidden unprivileged details.
    return (
        "// DPE direct screen capture; enabled explicitly in Screensharing settings.\n"
        "polkit.addRule(function(action, subject) {\n"
        f"  if (action.id === {json.dumps(ACTION)} &&\n"
        f"      subject.user === {json.dumps(username)} && subject.local && subject.active)\n"
        "    return polkit.Result.YES;\n"
        "});\n"
    )


def read_payload() -> bytes:
    # No payload path is accepted as an argument. Copy only this application's
    # bundled, checksummed ELF. Never follow a payload/manifest symlink as root.
    def read(name: str, maximum: int) -> bytes:
        fd = os.open(BUNDLE / name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= maximum:
                raise ValueError("Invalid bundled capture helper")
            data = stream.read(maximum + 1)
            if len(data) != info.st_size:
                raise ValueError("Changed bundled capture helper")
            return data

    manifest = json.loads(read("kms-server.json", 4096))
    data = read("dpe-kms-server", 2 * 1024 * 1024)
    if (
        not data.startswith(b"\x7fELF")
        or manifest.get("protocol") != 6
        or manifest.get("permissionVersion") != 2
        or manifest.get("sha256") != hashlib.sha256(data).hexdigest()
    ):
        raise ValueError("Bundled capture helper checksum mismatch")
    return data


def change(uid: int, enabled: bool):
    username = pwd.getpwuid(uid).pw_name
    data = read_payload() if enabled else None
    root_directory(ROOT)
    root_directory(ROOT / "permissions")
    root_directory(RULES)
    if enabled:
        root_directory(ACTIONS)
    lock = os.open(ROOT / ".setup.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(lock)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError("Untrusted permission lock")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        marker = ROOT / "permissions" / str(uid)
        grant = RULES / f"50-discord-patched-electron-kms-{uid}.rules"
        if enabled:
            atomic_write(SERVER, data, 0o755)
            atomic_write(ACTIONS / f"{ACTION}.policy", POLICY.encode(), 0o644)
            atomic_write(grant, rule(username).encode(), 0o644)
            atomic_write(marker, MARKER, 0o644)  # Ready only after helper+policy+rule.
        else:
            # Removing the marker immediately prevents new helper starts and
            # wakes active helpers' inotify watches before the rule reloads.
            marker.unlink(missing_ok=True)
            grant.unlink(missing_ok=True)
    finally:
        os.close(lock)


def main() -> int:
    if os.geteuid() != 0 or len(sys.argv) != 2 or sys.argv[1] not in ("--allow", "--revoke"):
        return 1
    identity = os.environ.get("PKEXEC_UID", "")
    if not re.fullmatch(r"[1-9][0-9]{0,9}", identity):
        return 1
    enabled = sys.argv[1] == "--allow"
    try:
        change(int(identity), enabled)
    except (OSError, ValueError, KeyError):
        print("DPE direct capture permission change failed", file=sys.stderr)
        return 1
    print(json.dumps({"allowed": enabled}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
