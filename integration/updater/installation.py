"""Publish a prepared fork into Discord's native app-version/installer.db layout.

Installers discover real app-version directories; prepared payloads remain private.
Native updater metadata describes the actual installed (patched) bytes. Existing
stock directories and launchers are moved to a recoverable backup, never deleted.
"""

import fcntl
import hashlib
import json
import os
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


def intact(directory: Path, state: dict, *, allow_mod=False) -> bool:
    try:
        expected = read_object(directory / "installer-record.json")
        actual = manifest(directory, exclude=("modules", "installer-record.json"))
        wanted = expected["distro_manifest"]
        if (
            allow_mod
            and "resources/_app.asar" not in wanted["files"]
            and (directory / "resources/_app.asar").is_file()
        ):
            # Default Vencord/Equicord installers wrap app.asar and retain its
            # original as _app.asar. Only this deliberate mutation is exempt.
            original = actual["files"].pop("resources/_app.asar")
            actual["files"]["resources/app.asar"] = original
        if actual != wanted:
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


def sync_directory(directory: Path):
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def recover(profile: Path, connection):
    """Roll back an interrupted activation unless SQLite committed its token.

    Every previous directory is retained, including an interrupted new tree.
    Re-running recovery after another interruption is safe.
    """
    journal = profile / "dpe-updater/activation.json"
    if not journal.exists():
        return
    transaction = read_object(journal)
    row = connection.execute(
        "SELECT value FROM key_values WHERE key=?", (transaction["key"],)
    ).fetchone()
    committed = row and json.loads(row[0]).get("activation") == transaction["id"]
    if not committed:
        for change in reversed(transaction["changes"]):
            name, saved = Path(change["path"]), Path(change["saved"])
            if saved.exists() or saved.is_symlink():
                if name.exists() or name.is_symlink():
                    name.rename(saved.with_name(saved.name + "-interrupted-" + uuid.uuid4().hex))
                saved.rename(name)
            elif not change["existed"] and (name.exists() or name.is_symlink()):
                name.rename(saved.with_name(saved.name + "-interrupted-" + uuid.uuid4().hex))
            sync_directory(saved.parent)
    sync_directory(profile)
    journal.unlink()
    sync_directory(journal.parent)


def publish_locked(app: Path, profile: Path, host: Path, executable: Path) -> Path:
    state = read_object(host / "client.json")
    public = profile / ("app-" + state["version"])
    entry = profile / EXECUTABLES[state["channel"]]
    key = "host/app/" + state["channel"] + "/linux/x64"
    active_key = "dpe/active/" + state["channel"]
    database = profile / "installer.db"
    existed = database.exists()
    connection = sqlite3.connect(database, timeout=30, isolation_level=None)
    staged = None
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS key_values (key TEXT NOT NULL PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute("BEGIN IMMEDIATE")
        recover(profile, connection)
        view = prepare_view(app, profile, host, executable, state)
        row = connection.execute(
            "SELECT value FROM key_values WHERE key=?", (active_key,)
        ).fetchone()
        active = json.loads(row[0]) if row else {}
        if (
            active.get("view") == str(view)
            and public.is_dir()
            and not public.is_symlink()
            and intact(public, state, allow_mod=True)
            and entry.is_symlink()
            and entry.resolve() == public / EXECUTABLES[state["channel"]]
        ):
            connection.execute("COMMIT")
            return public
        row = connection.execute("SELECT value FROM key_values WHERE key=?", (key,)).fetchone()
        previous = json.loads(row[0]) if row else []
        if not isinstance(previous, list):
            raise ValueError("Unsupported Discord installer database")
        token = uuid.uuid4().hex
        backup = profile / "dpe-updater/backups" / token
        backup.mkdir(parents=True)
        if existed:
            with (
                sqlite3.connect(database) as source,
                sqlite3.connect(backup / "installer.db") as copy,
            ):
                source.backup(copy)
        # Real directories are required by both official mod installers. Keep
        # modules anchored to their prepared view across a same-version update.
        staged = backup / "incoming"
        staged.mkdir()
        for item in view.iterdir():
            if item.name == "modules":
                (staged / "modules").symlink_to(item, target_is_directory=True)
            else:
                copy_payload(item, staged / item.name)
        old_resources = (
            entry.resolve().parent / "resources" if entry.exists() else public / "resources"
        )
        if (old_resources / "_app.asar").is_file() and (old_resources / "app.asar").is_file():
            resources = staged / "resources"
            # Refresh the underlying bootstrap while retaining the user's loader.
            if not (resources / "_app.asar").exists():
                (resources / "app.asar").rename(resources / "_app.asar")
            shutil.copy2(old_resources / "app.asar", resources / "app.asar")
        incoming = record(staged, state)
        # Keep the copied integrity record for the unwrapped bootstrap; the
        # native database below records the actual installed loader bytes.
        records = [item for item in previous if item["host_version"] != incoming["host_version"]]
        records.append(incoming)
        transaction = {
            "id": token,
            "key": active_key,
            "changes": [
                {
                    "path": str(name),
                    "saved": str(backup / name.name),
                    "existed": name.exists() or name.is_symlink(),
                }
                for name in (public, entry)
            ],
        }
        # Flush the prepared payload before recording a recoverable activation.
        # sync -f is Linux coreutils, already needed for reflink-aware copies.
        subprocess.run(["sync", "-f", str(backup)], check=True)
        write_json(profile / "dpe-updater/activation.json", transaction)
        sync_directory(profile / "dpe-updater")
        for change in transaction["changes"]:
            name = Path(change["path"])
            if change["existed"]:
                name.rename(change["saved"])
            if name == public:
                staged.rename(public)
            else:
                replace_link(entry, public / EXECUTABLES[state["channel"]])
        sync_directory(profile)
        sync_directory(backup)
        connection.execute(
            "INSERT OR IGNORE INTO key_values VALUES ('install_id', ?)",
            (json.dumps(str(uuid.uuid4())),),
        )
        for name, value in (
            (key, records),
            (
                active_key,
                {
                    "generation": host.name,
                    "view": str(view),
                    "version": state["version"],
                    "activation": token,
                },
            ),
        ):
            connection.execute(
                "INSERT OR REPLACE INTO key_values VALUES (?, ?)", (name, json.dumps(value))
            )
        connection.execute("COMMIT")
        (profile / "dpe-updater/activation.json").unlink()
        sync_directory(profile / "dpe-updater")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        recover(profile, connection)
        raise
    finally:
        connection.close()
        # A failed copy must not accumulate another partial payload on each
        # automatic retry. This is only our unpublished staging directory.
        if staged is not None and staged.exists():
            shutil.rmtree(staged)
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
