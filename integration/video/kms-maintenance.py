#!/usr/bin/python3 -I
# SPDX-License-Identifier: MIT
"""Root-owned KMS maintenance: fixed destinations and independently verified code."""

from __future__ import annotations

import fcntl
import hashlib
import http.client
import importlib.util
import json
import os
import re
import select
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path("/usr/local/libexec/discord-patched-electron")
ACTION = "org.discord-patched-electron.capture.maintain"
REPOSITORY = "discord-patched-electron/discord-patched-electron"
ROLES = {
    "server": ("capture/dpe-kms-server", 2 * 1024 * 1024),
    "maintainer": ("integration/video/kms-maintenance.py", 128 * 1024),
    "installer": ("integration/video/kms-permission-setup.py", 128 * 1024),
}
HEX = re.compile(r"[a-f0-9]{64}")


def read_file(path: Path, limit: int, *, root=False) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or not 0 < info.st_size <= limit
            or (root and (info.st_uid != 0 or info.st_mode & 0o022))
        ):
            raise ValueError("Invalid maintenance file")
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        if len(data) != info.st_size or (
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        ) != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError("Maintenance file changed")
        return data


def setup_module():
    # -I excludes user Python paths; the sole application import is installed,
    # root-owned code, never the candidate application's Python updater.
    source = ROOT / "kms-permission-setup.py"
    read_file(source, 128 * 1024, root=True)
    spec = importlib.util.spec_from_file_location("dpe_root_kms_setup", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def record_path(uid):
    return ROOT / "maintenance" / str(uid)


def descriptor(app: Path, runtime_digest: str) -> tuple[dict, dict]:
    payloads = {role: read_file(app / relative, limit) for role, (relative, limit) in ROLES.items()}
    if not payloads["server"].startswith(b"\x7fELF"):
        raise ValueError("Invalid capture executable")
    return {
        "runtime": runtime_digest,
        **{role: hashlib.sha256(data).hexdigest() for role, data in payloads.items()},
    }, payloads


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if not newurl.startswith("https://"):
            raise ValueError("Insecure release redirect")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def fetch(url, maximum):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), SafeRedirect())
    with opener.open(url, timeout=8) as response:
        if url.startswith("https://raw.githubusercontent.com/") and response.url != url:
            raise ValueError("Unexpected approval redirect")
        data = response.read(maximum + 1)
        if not 0 < len(data) <= maximum:
            raise ValueError("Oversized release proof")
        return data


def approved_release(policy: dict) -> dict:
    channel, track = policy["channel"], policy["track"]
    if channel not in ("canary", "ptb", "stable") or track not in ("prerelease", "stable"):
        raise ValueError("Invalid release channel")
    filename = (
        "approved-release.json"
        if (channel, track) == ("canary", "prerelease")
        else f"approved-{channel}-{track}-release.json"
    )
    approval = json.loads(
        fetch(f"https://raw.githubusercontent.com/{REPOSITORY}/main/config/{filename}", 16 * 1024)
    )
    tag = approval.get("tag", "")
    if (
        approval.get("schema") != 1
        or not re.fullmatch(r"v[0-9A-Za-z.+-]{1,200}", tag)
        or not HEX.fullmatch(approval.get("manifest_sha256", ""))
        or not re.fullmatch(r"\d{8}\.\d{6}", approval.get("version", ""))
        or not isinstance(approval.get("approved_by"), str)
        or not approval["approved_by"].strip()
    ):
        raise ValueError("Invalid release approval")
    files = {
        name: fetch(f"https://github.com/{REPOSITORY}/releases/download/{tag}/{name}", maximum)
        for name, maximum in (
            ("release.json", 4 * 1024 * 1024),
            ("SHA256SUMS", 1024 * 1024),
            ("SHA256SUMS.sig", 64 * 1024),
        )
    }
    verify_signature(files)
    digest = hashlib.sha256(files["release.json"]).hexdigest()
    checksums = {}
    for line in files["SHA256SUMS"].decode().splitlines():
        match = re.fullmatch(r"([a-f0-9]{64})  ([A-Za-z0-9._+-]+)", line)
        if not match or match[2] in checksums:
            raise ValueError("Invalid signed checksums")
        checksums[match[2]] = match[1]
    manifest = json.loads(files["release.json"])
    version = manifest.get("electron_version", "")
    expected_track = (
        "prerelease" if re.fullmatch(r"\d+\.\d+\.\d+-(alpha|beta)\.\d+", version) else "stable"
    )
    if (
        checksums.get("release.json") != digest
        or digest != approval["manifest_sha256"]
        or manifest.get("schema") != 1
        or manifest.get("signing_required") is not True
        or manifest.get("source_dirty") is not False
        or manifest.get("version") != approval["version"]
        or manifest.get("channel") != channel
        or expected_track != track
    ):
        raise ValueError("Release not approved for maintenance")
    return manifest


def verify_signature(files):
    key = read_file(ROOT / "release-signing-key.asc", 64 * 1024, root=True)
    with tempfile.TemporaryDirectory(prefix="dpe-kms-proof-") as temporary:
        directory = Path(temporary)
        for name, data in {**files, "key.asc": key}.items():
            (directory / name).write_bytes(data)
        environment = {"PATH": "/usr/bin", "LANG": "C"}
        subprocess.run(
            [
                "/usr/bin/gpg",
                "--batch",
                "--homedir",
                temporary,
                "--dearmor",
                "--output",
                str(directory / "key.gpg"),
                str(directory / "key.asc"),
            ],
            env=environment,
            check=True,
            capture_output=True,
            timeout=10,
        )
        subprocess.run(
            [
                "/usr/bin/gpgv",
                "--homedir",
                temporary,
                "--keyring",
                str(directory / "key.gpg"),
                str(directory / "SHA256SUMS.sig"),
                str(directory / "SHA256SUMS"),
            ],
            env=environment,
            check=True,
            capture_output=True,
            timeout=10,
        )


def authorize(uid, pid, endpoint, token, app):
    if not re.fullmatch(r"[a-f0-9]{64}", token):
        raise ValueError("Invalid worker capability")
    peer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        peer.settimeout(2)
        peer.connect(endpoint)
        credentials = struct.unpack(
            "3i", peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        )
        if credentials[:2] != (pid, uid):
            raise ValueError("Wrong maintenance caller")
        peer.sendall((token + "\n").encode())
        reply = bytearray()
        while len(reply) <= 4096 and reply.count(b"\n") < 2:
            byte = peer.recv(1)
            if not byte:
                raise ValueError("Maintenance caller disconnected")
            reply.extend(byte)
        expected = os.fsencode(ROOT / "kms-maintenance") + b"\n" + os.fsencode(app) + b"\n"
        if reply != expected:
            raise ValueError("Wrong maintenance worker or active application")
        return peer
    except BaseException:
        peer.close()
        raise


def runtime_identity(setup, uid, pid):
    binding = setup.executable_grant(uid, pid)
    # A pathname alone never chooses a runtime. Hash the live kernel executable,
    # then recheck the inode/change metadata used by the grant.
    with setup.as_user(uid), Path(f"/proc/{pid}/exe").open("rb") as executable:
        digest = hashlib.file_digest(executable, "sha256").hexdigest()
    if setup.executable_grant(uid, pid) != binding:
        raise ValueError("Running executable changed")
    return binding, digest


def refresh(setup, uid, pid, app, endpoint, token, *, revoke=False):
    if not app.is_absolute():
        raise ValueError("Invalid application directory")
    with authorize(uid, pid, endpoint, token, app) as caller:
        policy = json.loads(read_file(record_path(uid), 16384, root=True))
        if policy.get("schema") != 1 or policy.get("enabled") is not True:
            raise ValueError("Maintenance not enabled")
        binding, runtime_digest = runtime_identity(setup, uid, pid)
        with setup.as_user(uid):
            incoming, payloads = descriptor(app, runtime_digest)
        global_authority = json.loads(
            read_file(ROOT / "maintenance-authority.json", 16384, root=True)
        )
        installed = {role: incoming[role] for role in ROLES}
        if incoming != policy.get("current") or installed != global_authority.get("components"):
            release = approved_release(policy)
            capture = release.get("capture_maintenance", {})
            if capture.get("schema") != 1 or capture.get("components") != incoming:
                raise ValueError("Capture update is not covered by the approved release")
            previous = policy.get("release_version")
            global_version = global_authority.get("release_version")
            if (
                (previous and release["version"] < previous)
                or (global_version and release["version"] < global_version)
                or (previous == release["version"] and incoming != policy.get("current"))
                or (
                    global_version == release["version"]
                    and installed != global_authority.get("components")
                )
            ):
                raise ValueError("Capture update would roll back its authority")
            policy.update(current=incoming, release_version=release["version"])
        if select.select([caller], [], [], 0)[0] or setup.executable_grant(uid, pid) != binding:
            raise ValueError("Maintenance caller changed or exited")
        # All validation precedes revocation or replacement. Never run candidate
        # code. These bytes have either initial-admin or signed-release authority.
        if revoke:
            setup.apply(uid, None, None)
        else:
            setup.apply(
                uid,
                payloads["server"],
                binding,
                maintenance=(payloads["maintainer"], payloads["installer"], policy),
            )


def main():
    if os.geteuid() != 0 or len(sys.argv) != 6 or sys.argv[1] not in ("--refresh", "--revoke"):
        return 1
    uid = os.environ.get("PKEXEC_UID", "")
    if not re.fullmatch(r"[1-9][0-9]{0,9}", uid) or not re.fullmatch(
        r"[1-9][0-9]{0,9}", sys.argv[2]
    ):
        return 1
    try:
        setup = setup_module()
        setup.root_directory(ROOT)
        # Serialize refresh/initial install/revoke with the same fixed lock.
        fd = os.open(ROOT / ".setup.lock", os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(fd, "wb") as lock:
            info = os.fstat(lock.fileno())
            if info.st_uid != 0 or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022:
                raise ValueError("Untrusted maintenance lock")
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            refresh(
                setup,
                int(uid),
                int(sys.argv[2]),
                Path(sys.argv[3]),
                sys.argv[4],
                sys.argv[5],
                revoke=sys.argv[1] == "--revoke",
            )
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
        http.client.HTTPException,
    ):
        print("DPE KMS maintenance rejected", file=sys.stderr)
        return 1
    print(json.dumps({"updated": True}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
