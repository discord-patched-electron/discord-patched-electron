"""Remove DPE's system capture authorization; leave user profiles untouched."""

import argparse
import errno
import fcntl
import json
import os
import re
import stat
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

SYSTEM_ROOT = Path("/")
ROOT_UID = 0
KMS_ROOT = Path("/usr/local/libexec/discord-patched-electron")
RULES = Path("/etc/polkit-1/rules.d")
ACTIONS = Path("/usr/share/polkit-1/actions")
APPARMOR = Path("/etc/apparmor.d")
APPARMOR_PROFILES = Path("/sys/kernel/security/apparmor/profiles")
APPARMOR_PARSER = Path("/sbin/apparmor_parser")
INSTALL_BASES = (Path("/opt"), Path("/usr/lib"))
PACKAGE = re.compile(
    r"discord-(stable|canary|ptb)-(stable|prerelease|nightly)-patched-electron-bin"
)
UID = re.compile(r"[0-9]{1,10}")
RULE = re.compile(r"50-discord-patched-electron-kms-([0-9]{1,10})\.rules")
GLOBAL_FILES = (
    "kms-server",
    "kms-maintenance",
    "kms-permission-setup.py",
    "release-signing-key.asc",
    "maintenance-authority.json",
)


def directory(path: Path) -> bool:
    """Do not traverse writable or linked system directories as root."""
    for parent in reversed((path, *path.parents)):
        if not parent.is_relative_to(SYSTEM_ROOT):
            continue
        try:
            info = parent.lstat()
        except FileNotFoundError:
            return False
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != ROOT_UID or info.st_mode & 0o022:
            raise ValueError(f"Untrusted system directory: {parent}")
    return True


def regular(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISREG(info.st_mode) and info.st_uid == ROOT_UID and not info.st_mode & 0o022


def installations(own_root: Path) -> set[str]:
    remaining = set()
    for base in INSTALL_BASES:
        if not directory(base):
            continue
        for candidate in base.iterdir():
            if candidate == own_root or not PACKAGE.fullmatch(candidate.name):
                continue
            if candidate.is_symlink():
                continue
            if directory(candidate) and any(
                regular(candidate / name) for name in ("bootstrap.json", "client-distribution.json")
            ):
                remaining.add(candidate.name)
    return remaining


def empty_directory(path: Path):
    try:
        path.rmdir()
    except FileNotFoundError:
        pass
    except OSError as error:
        if error.errno != errno.ENOTEMPTY:
            raise


@contextmanager
def locked():
    if not directory(KMS_ROOT):
        yield False
        return
    fd = os.open(
        KMS_ROOT / ".setup.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600
    )
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != ROOT_UID or info.st_mode & 0o022:
            raise ValueError("Untrusted permission lock")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield True
    finally:
        os.close(fd)


def scoped_uids(package: str) -> set[str]:
    records = KMS_ROOT / "maintenance"
    if not directory(records):
        return set()
    match = PACKAGE.fullmatch(package)
    users = set()
    for record in records.iterdir():
        if not UID.fullmatch(record.name) or not regular(record) or record.stat().st_size > 16384:
            continue
        try:
            state = json.loads(record.read_bytes())
        except (ValueError, UnicodeError):
            continue
        if isinstance(state, dict) and (state.get("channel"), state.get("track")) == match.groups():
            users.add(record.name)
    return users


def clean_children(path: Path, selected):
    # Known directory symlinks are removed themselves; never follow their target.
    if path.is_symlink():
        path.unlink()
        return
    if directory(path):
        for child in path.iterdir():
            if selected(child.name) and (child.is_symlink() or not child.is_dir()):
                child.unlink(missing_ok=True)
        empty_directory(path)


def clean_apparmor(package: str | None, full: bool):
    if not directory(APPARMOR):
        return
    try:
        loaded = {line.split(" (", 1)[0] for line in APPARMOR_PROFILES.read_text().splitlines()}
    except FileNotFoundError:
        loaded = set()
    for profile in APPARMOR.iterdir():
        if not PACKAGE.fullmatch(profile.name) or not (full or profile.name == package):
            continue
        if profile.name in loaded:
            if not regular(profile):
                raise ValueError("Untrusted DPE AppArmor profile")
            subprocess.run([str(APPARMOR_PARSER), "-R", str(profile)], check=True)
        profile.unlink(missing_ok=True)


def cleanup(package: str | None, own_root: Path):
    if package is not None and not PACKAGE.fullmatch(package):
        raise ValueError("Invalid DPE package name")
    with locked() as has_root:
        remaining = installations(own_root)
        if remaining and (package is None or package in remaining):
            return
        full = not remaining
        users = set() if full or not has_root else scoped_uids(package)
        # Validate every parent before deleting anything. Revocation precedes
        # rule/helper removal so live servers lose their grant immediately.
        rules_exist = directory(RULES)
        actions_exist = directory(ACTIONS)
        if has_root:
            for name in ("permissions", "maintenance", "__pycache__"):
                path = KMS_ROOT / name
                if not path.is_symlink():
                    directory(path)
            for name in ("permissions", "maintenance"):
                clean_children(
                    KMS_ROOT / name,
                    lambda name: bool(UID.fullmatch(name)) if full else name in users,
                )
        if rules_exist:
            for path in RULES.iterdir():
                match = RULE.fullmatch(path.name)
                if match and (full or match[1] in users):
                    path.unlink(missing_ok=True)
        clean_apparmor(package, full)
        if not full:
            return
        if actions_exist:
            for name in (
                "org.discord-patched-electron.capture.policy",
                "org.discord-patched-electron.capture.maintain.policy",
            ):
                (ACTIONS / name).unlink(missing_ok=True)
        if has_root:
            for name in GLOBAL_FILES:
                (KMS_ROOT / name).unlink(missing_ok=True)
            clean_children(
                KMS_ROOT / "__pycache__",
                lambda name: bool(
                    re.fullmatch(
                        r"kms-(permission-setup|maintenance)\.cpython-\d+(?:\.opt-\d+)?\.pyc", name
                    )
                ),
            )
        # Interrupted atomic writes use this DPE-specific prefix in these dirs.
        for path in (KMS_ROOT / "permissions", KMS_ROOT / "maintenance", KMS_ROOT):
            if has_root:
                clean_children(path, lambda name: name.startswith(".dpe-kms-"))
        for path, exists in ((RULES, rules_exist), (ACTIONS, actions_exist)):
            if exists:
                for child in path.glob(".dpe-kms-*"):
                    if not child.is_dir():
                        child.unlink(missing_ok=True)
        if has_root:
            (KMS_ROOT / ".setup.lock").unlink(missing_ok=True)
            empty_directory(KMS_ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--package", help="Package-manager removal; preserve other installed channels"
    )
    args = parser.parse_args()
    if os.geteuid() != 0:
        print("System capture cleanup requires root (run with sudo).", file=sys.stderr)
        return 1
    try:
        cleanup(args.package, Path(__file__).resolve().parent)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"DPE capture cleanup failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
