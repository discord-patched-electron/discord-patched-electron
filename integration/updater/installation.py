"""Publish a prepared fork into Discord's native app-version/installer.db layout.

Payloads stay deduplicated behind an atomically replaced app-version symlink.
Native updater metadata describes the actual installed (patched) bytes. Existing
stock directories and launchers are moved to a recoverable backup, never deleted.
"""

import fcntl
import hashlib
import json
import re
import shlex
import shutil
import sqlite3
import subprocess
import tempfile
import uuid
from pathlib import Path

from support import checksum, read_object, write_json

EXECUTABLES = {"stable": "Discord", "canary": "DiscordCanary", "ptb": "DiscordPTB"}


def manifest(directory: Path, *, exclude=()) -> dict:
    files = {}
    for item in sorted(directory.rglob("*")):
        relative = item.relative_to(directory).as_posix()
        if relative.split("/", 1)[0] in exclude:
            continue
        if item.is_file():
            files[relative] = {"New": {"Sha256": checksum(item)}}
    return {"manifest_version": 1, "files": files}


def intact(directory: Path, state: dict) -> bool:
    try:
        expected = read_object(directory / "installer-record.json")
        if (
            manifest(directory, exclude=("modules", "installer-record.json"))
            != expected["distro_manifest"]
        ):
            return False
        for module in expected["modules"]:
            version = module["module_version"]
            name = version["module"]["name"]
            if (
                name not in state["modules"]
                or version["version"] != state["modules"][name]["version"]
            ):
                return False
            if (
                manifest(directory / "modules" / f"{name}-{version['version']}" / name)
                != module["distro_manifest"]
            ):
                return False
        return len(expected["modules"]) == len(state["modules"])
    except (OSError, ValueError, KeyError, TypeError):
        return False


def copy_payload(source: Path, target: Path):
    # A stock updater may modify these files. Reflinks/copies isolate its writes
    # from the signed runtime cache, installed package and prepared module cache.
    subprocess.run(["cp", "-a", "--reflink=auto", "--", str(source), str(target)], check=True)


def record(directory: Path, state: dict) -> dict:
    host = {
        "host": {
            "name": "app",
            "release_channel": state["channel"],
            "platform": "linux",
            "arch": "x64",
        },
        "version": list(map(int, state["version"].split("."))),
    }
    return {
        "host_version": host,
        "install_state": "Installed",
        "distro_manifest": manifest(directory, exclude=("modules", "installer-record.json")),
        "modules": [
            {
                "module_version": {
                    "module": {"host_version": host, "name": name},
                    "version": source["version"],
                },
                "install_state": "Installed",
                "distro_manifest": manifest(
                    (directory / "modules" / f"{name}-{source['version']}" / name).resolve()
                ),
            }
            for name, source in state["modules"].items()
        ],
    }


def prepare_view(app: Path, profile: Path, host: Path, executable: Path, state: dict) -> Path:
    if state["channel"] not in EXECUTABLES or not re.fullmatch(r"\d+\.\d+\.\d+", state["version"]):
        raise ValueError("Invalid native installation identity")
    # A mod reinstall or relocated portable package also refreshes the public view.
    identity = hashlib.sha256(
        json.dumps(
            {
                "host": str(host),
                "runtime": str(executable),
                "runtime_files": manifest(executable.parent, exclude=("resources", "modules")),
                "app": str(app),
                "resources": manifest(app / "resources"),
                "camera": checksum(app / "libdpe-camera-probe.so"),
                "adapter": checksum(Path(__file__)),
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    views = profile / "dpe-updater/installations"
    views.mkdir(parents=True, exist_ok=True)
    destination = views / identity
    for candidate in (destination, *sorted(views.glob(identity + "-*"))):
        if intact(candidate, state):
            return candidate
    if destination.exists():
        destination = views / (identity + "-" + uuid.uuid4().hex)
    with tempfile.TemporaryDirectory(prefix="install-", dir=views) as temporary:
        staged = Path(temporary) / "host"
        staged.mkdir()
        # Only the selected fork is installed; no stock host is downloaded.
        for item in executable.parent.iterdir():
            if item.name not in ("resources", "modules"):
                copy_payload(item, staged / item.name)
        preload = staged / "preload"
        preload.mkdir()
        shutil.copy2(app / "libdpe-camera-probe.so", preload / "libdpe-camera-probe.so")
        resources = staged / "resources"
        shutil.copytree(app / "resources", resources)
        shutil.copy2(host / "resources/build_info.json", resources / "build_info.json")
        shutil.copy2(host / "client.json", staged / "client.json")
        for name, source in state["modules"].items():
            parent = staged / "modules" / f"{name}-{source['version']}"
            parent.mkdir(parents=True)
            copy_payload((host / "modules" / name).resolve(), parent / name)
        launcher = staged / EXECUTABLES[state["channel"]]
        launcher.write_text(
            "#!/bin/sh\n"
            + "export DPE_PROFILE_DIR="
            + shlex.quote(str(profile))
            + "\nexec "
            + shlex.quote(str(app / "launch.sh"))
            + ' "$@"\n'
        )
        launcher.chmod(0o700)
        write_json(staged / "installer-record.json", record(staged, state))
        staged.rename(destination)
    return destination


def replace_link(path: Path, target: Path):
    temporary = path.with_name(path.name + ".dpe-" + uuid.uuid4().hex)
    try:
        temporary.symlink_to(target)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def publish(app: Path, profile: Path, host: Path, executable: Path) -> Path:
    app, profile, host, executable = (p.absolute() for p in (app, profile, host, executable))
    private = profile / "dpe-updater"
    private.mkdir(parents=True, exist_ok=True)
    with (private / "installation.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return publish_locked(app, profile, host, executable)


def publish_locked(app: Path, profile: Path, host: Path, executable: Path) -> Path:
    state = read_object(host / "client.json")
    view = prepare_view(app, profile, host, executable, state)
    public = profile / ("app-" + state["version"])
    entry = profile / EXECUTABLES[state["channel"]]
    key = "host/app/" + state["channel"] + "/linux/x64"
    database = profile / "installer.db"
    backup = profile / "dpe-updater/backups" / uuid.uuid4().hex
    existed = database.exists()
    connection = sqlite3.connect(database, timeout=30, isolation_level=None)
    changes = []
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS key_values (key TEXT NOT NULL PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute("BEGIN IMMEDIATE")
        takeovers = []
        for name in (public, entry):
            if not name.exists() and not name.is_symlink():
                continue
            owned = name.is_symlink() and (
                name == public
                and name.resolve().is_relative_to(view.parent)
                or name == entry
                and name.readlink() == public / EXECUTABLES[state["channel"]]
            )
            if not owned:
                takeovers.append(name)
        if takeovers:
            backup.mkdir(parents=True)
            if existed:
                # Read the committed pre-takeover database through a separate
                # connection; backing up a connection with a write lock hangs.
                with (
                    sqlite3.connect(database) as source,
                    sqlite3.connect(backup / "installer.db") as copy,
                ):
                    source.backup(copy)
        row = connection.execute("SELECT value FROM key_values WHERE key=?", (key,)).fetchone()
        previous = json.loads(row[0]) if row else []
        if not isinstance(previous, list):
            raise ValueError("Unsupported Discord installer database")
        incoming = read_object(view / "installer-record.json")
        records = [item for item in previous if item["host_version"] != incoming["host_version"]]
        records.append(incoming)
        for name, target in ((public, view), (entry, public / EXECUTABLES[state["channel"]])):
            old = name.readlink() if name.is_symlink() else None
            saved = None
            if name in takeovers:
                saved = backup / name.name
                name.rename(saved)
                old = None
            changes.append((name, old, saved))
            replace_link(name, target)
        connection.execute(
            "INSERT OR IGNORE INTO key_values VALUES ('install_id', ?)",
            (json.dumps(str(uuid.uuid4())),),
        )
        for name, value in (
            (key, records),
            (
                "dpe/active/" + state["channel"],
                {"generation": host.name, "view": str(view), "version": state["version"]},
            ),
        ):
            connection.execute(
                "INSERT OR REPLACE INTO key_values VALUES (?, ?)", (name, json.dumps(value))
            )
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        for name, old, saved in reversed(changes):
            if old is not None:
                replace_link(name, old)
            else:
                name.unlink(missing_ok=True)
                if saved is not None:
                    saved.rename(name)
        raise
    finally:
        connection.close()
    return public


if __name__ == "__main__":
    import sys

    app, profile, host, executable = map(Path, sys.argv[1:])
    installed = publish(app, profile, host, executable)
    state = read_object(host / "client.json")
    voice = (
        installed
        / "modules"
        / f"discord_voice-{state['modules']['discord_voice']['version']}"
        / "discord_voice"
    )
    print(installed / "electron")
    print(voice)
