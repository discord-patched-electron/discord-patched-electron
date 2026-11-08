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
MAINTENANCE_ACTION = ACTION + ".maintain"
BUNDLE = Path(__file__).resolve().parents[2] / "capture"
MARKER = b"DPE-KMS-3\n"
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
  <action id="{MAINTENANCE_ACTION}">
    <description>Maintain approved DPE capture helpers</description>
    <message>Maintain approved DPE capture helpers.</message>
    <defaults><allow_any>no</allow_any><allow_inactive>no</allow_inactive><allow_active>no</allow_active></defaults>
    <annotate key="org.freedesktop.policykit.exec.path">{ROOT / "kms-maintenance"}</annotate>
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
        f"  if ((action.id === {json.dumps(ACTION)} ||\n"
        f"       action.id === {json.dumps(MAINTENANCE_ACTION)}) &&\n"
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
        or manifest.get("permissionVersion") != 4
        or manifest.get("sha256") != hashlib.sha256(data).hexdigest()
    ):
        raise ValueError("Bundled capture helper checksum mismatch")
    return data


def executable_grant(uid: int, pid: int) -> bytes:
    # Read the live caller through procfs, never an executable path supplied by
    # the renderer. Pin its inode and change timestamps; replacing or modifying
    # that executable invalidates the grant even if its pathname is reused.
    proc = Path(f"/proc/{pid}")
    if proc.stat().st_uid != uid:
        raise ValueError("Wrong Discord owner")
    executable = proc / "exe"
    name = os.readlink(executable)
    if not name.startswith("/") or "\n" in name or name.endswith(" (deleted)"):
        raise ValueError("Invalid Discord executable")
    with executable.open("rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid not in (0, uid)
            or info.st_mode & 0o022
            or not 0 < info.st_size <= 512 * 1024 * 1024
            or stream.read(4) != b"\x7fELF"
        ):
            raise ValueError("Invalid Discord executable")

        def identity(st):
            return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)

        fields = identity(info)
        if identity(Path(name).stat()) != fields or identity(executable.stat()) != fields:
            raise ValueError("Discord executable changed")
    if proc.stat().st_uid != uid or os.readlink(executable) != name:
        raise ValueError("Discord process changed")
    return MARKER + ("\n".join(map(str, fields)) + "\n" + name + "\n").encode()


def read_component(path: Path, limit=128 * 1024):
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
            raise ValueError("Invalid maintenance component")
        data = stream.read(limit + 1)
        if len(data) != info.st_size:
            raise ValueError("Maintenance component changed")
        return data


def bootstrap_maintenance(uid, pid, data, binding):
    app = BUNDLE.parent
    maintainer = read_component(app / "integration/video/kms-maintenance.py")
    installer = read_component(Path(__file__).resolve())
    key = read_component(app / "integration/updater/release-signing-key.asc", 64 * 1024)
    runtime_policy = json.loads(read_component(app / "runtime-update.json", 64 * 1024))
    with Path(f"/proc/{pid}/exe").open("rb") as executable:
        runtime = hashlib.file_digest(executable, "sha256").hexdigest()
    if executable_grant(uid, pid) != binding:
        raise ValueError("Running executable changed")
    channel, track = runtime_policy["channel"], runtime_policy["electron_channel"]
    if channel not in ("canary", "ptb", "stable") or track not in ("stable", "prerelease"):
        raise ValueError("Invalid update channel")
    policy = {
        "schema": 1,
        "enabled": True,
        "channel": channel,
        "track": track,
        "release_version": None,
        "current": {
            "runtime": runtime,
            "server": hashlib.sha256(data).hexdigest(),
            "maintainer": hashlib.sha256(maintainer).hexdigest(),
            "installer": hashlib.sha256(installer).hexdigest(),
        },
    }
    return maintainer, installer, policy, key


def apply(uid, data, binding, *, maintenance=None):
    marker = ROOT / "permissions" / str(uid)
    grant = RULES / f"50-discord-patched-electron-kms-{uid}.rules"
    record = ROOT / "maintenance" / str(uid)
    if data is not None:
        # Stop old exports before publishing any new privileged component.
        marker.unlink(missing_ok=True)
        atomic_write(SERVER, data, 0o744)  # root executes; ordinary users only read.
        if maintenance:
            maintainer, installer, policy, *key = maintenance
            atomic_write(ROOT / "kms-maintenance", maintainer, 0o700)
            atomic_write(ROOT / "kms-permission-setup.py", installer, 0o600)
            if key:
                atomic_write(ROOT / "release-signing-key.asc", key[0], 0o600)
            authority = {
                "schema": 1,
                "components": {
                    role: policy["current"][role] for role in ("server", "maintainer", "installer")
                },
                "release_version": policy.get("release_version"),
            }
            atomic_write(
                ROOT / "maintenance-authority.json", (json.dumps(authority) + "\n").encode(), 0o644
            )
            atomic_write(record, (json.dumps(policy) + "\n").encode(), 0o644)
        atomic_write(ACTIONS / f"{ACTION}.policy", POLICY.encode(), 0o644)
        atomic_write(grant, rule(pwd.getpwuid(uid).pw_name).encode(), 0o644)
        atomic_write(marker, binding, 0o644)
    else:
        if record.exists():
            state = json.loads(read_component(record, 16384))
            state["enabled"] = False
            atomic_write(record, (json.dumps(state) + "\n").encode(), 0o644)
        marker.unlink(missing_ok=True)
        grant.unlink(missing_ok=True)


def change(uid: int, enabled: bool, discord_pid: int | None = None):
    data = read_payload() if enabled else None
    binding = executable_grant(uid, discord_pid) if enabled and discord_pid else None
    if enabled and binding is None:
        raise ValueError("Discord identity required")
    maintenance = bootstrap_maintenance(uid, discord_pid, data, binding) if enabled else None
    root_directory(ROOT)
    root_directory(ROOT / "permissions")
    root_directory(ROOT / "maintenance")
    root_directory(RULES)
    if enabled:
        root_directory(ACTIONS)
    lock = os.open(ROOT / ".setup.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(lock)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError("Untrusted permission lock")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        apply(uid, data, binding, maintenance=maintenance)
    finally:
        os.close(lock)


def main() -> int:
    if os.geteuid() != 0 or len(sys.argv) not in (2, 3):
        return 1
    if sys.argv[1] == "--allow":
        if len(sys.argv) != 3 or not re.fullmatch(r"[1-9][0-9]{0,9}", sys.argv[2]):
            return 1
        discord_pid = int(sys.argv[2])
    elif sys.argv[1] == "--revoke" and len(sys.argv) == 2:
        discord_pid = None
    else:
        return 1
    identity = os.environ.get("PKEXEC_UID", "")
    if not re.fullmatch(r"[1-9][0-9]{0,9}", identity):
        return 1
    enabled = sys.argv[1] == "--allow"
    try:
        change(int(identity), enabled, discord_pid)
    except (OSError, ValueError, KeyError):
        print("DPE direct capture permission change failed", file=sys.stderr)
        return 1
    print(json.dumps({"allowed": enabled}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
