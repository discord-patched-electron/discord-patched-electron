"""Install official client mods without giving installers the live app directory."""

import argparse
import fcntl
import json
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import tempfile
import time
from pathlib import Path

from client_mods import detect
from support import RateLimited, checksum, download, read_object, write_json

# The same reviewed installer releases used by check-client-mods in CI.
INSTALLERS = {
    "vencord": (
        "https://github.com/Vencord/Installer/releases/download/v1.4.2/VencordInstallerCli-linux",
        "52642c8521a866ad241c10337144a4f513672c54b9c86a8169954785054c815d",
    ),
    "equicord": (
        "https://github.com/Equicord/Equilotl/releases/download/v2.3.0/EquilotlCli-linux",
        "1ee134c7295bbbb2ef7058d157509508832e17a543ac1bf753ca63b16d4a8bbd",
    ),
}
CHOICES = {"none", *INSTALLERS}
PAYLOAD_CACHE_SECONDS = 30 * 60
PAYLOAD_LIMIT = 256 * 1024 * 1024
REQUIRED_FILES = {"patcher.js", "preload.js", "renderer.js", "renderer.css", "package.json"}


def resources(profile: Path, channel: str) -> Path:
    """Only the active public installation is eligible, never an old backup."""
    database = profile / "installer.db"
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        row = connection.execute(
            "SELECT value FROM key_values WHERE key=?", ("dpe/active/" + channel,)
        ).fetchone()
    version = json.loads(row[0]).get("version") if row else None
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Finish Discord's first download, then reopen Mods.")
    result = profile / ("app-" + version) / "resources"
    if result.is_symlink() or result.parent.is_symlink():
        raise ValueError("The active Discord installation must be a local directory.")
    archive = result / "app.asar"
    if archive.is_symlink() or not archive.is_file():
        raise ValueError(
            "The active Discord installation is incomplete. Restart Discord to repair it."
        )
    return result


def preferences(profile: Path) -> dict:
    filename = profile / "dpe-updater/mods.json"
    try:
        return read_object(filename)
    except FileNotFoundError:
        return {}


def installed_version(mod) -> str | None:
    """Read the official build banner or package version without executing the mod."""
    if not mod:
        return None
    target = Path(mod.target)
    try:
        if target.is_symlink() or not target.is_file():
            return None
        if mod.name == "Vencord":
            with target.open("rb") as stream:
                banner = stream.read(160)

            def package():
                filename = target.parent / "package.json"
                if filename.is_symlink() or not filename.is_file():
                    return {}
                with filename.open("rb") as stream:
                    data = stream.read(16385)
                return json.loads(data) if len(data) <= 16384 else {}

        else:
            with target.open("rb") as stream:
                size, header_size, payload_size, json_size = struct.unpack("<4I", stream.read(16))
                total = target.stat().st_size
                if (
                    size != 4
                    or header_size != payload_size + 4
                    or header_size % 4
                    or not 0 < json_size <= payload_size - 4 <= 256 * 1024
                    or 8 + header_size > total
                ):
                    return None
                files = json.loads(stream.read(json_size))["files"]

            def member(name, limit):
                entry = files[name]
                if "link" in entry or entry.get("unpacked"):
                    return b""
                offset, length = int(entry["offset"]), entry["size"]
                start = 8 + header_size + offset
                if type(length) is not int or offset < 0 or length < 0 or start + length > total:
                    return b""
                with target.open("rb") as stream:
                    stream.seek(start)
                    return stream.read(min(length, limit))

            banner = member("patcher.js", 160)

            def package():
                data = member("package.json", 16385)
                return json.loads(data) if len(data) <= 16384 else {}

        match = re.match(rb"// " + mod.name.encode("ascii") + rb" ([0-9a-f]{7,40})\r?\n", banner)
        if match:
            return match[1].decode("ascii")[:7]
        version = package().get("version")
        if isinstance(version, str) and re.fullmatch(
            r"\d+\.\d+\.\d+(?:[-+][a-zA-Z0-9.-]+)?", version
        ):
            return version if len(version) <= 80 else None
    except (OSError, ValueError, KeyError, TypeError, AttributeError, struct.error):
        return None
    return None


def status(profile: Path, channel: str) -> dict:
    current = resources(profile, channel)
    mod = detect(current)
    return {
        "installed": mod.name.lower() if mod else "none",
        "installedVersion": installed_version(mod),
        "setupComplete": preferences(profile).get("setupComplete") is True,
        "onboardingPending": preferences(profile).get("onboardingPending") is True,
        "revision": checksum(current / "app.asar"),
    }


def complete(profile: Path, channel: str, *, pending: bool = False) -> dict:
    result = status(profile, channel)
    (profile / "dpe-updater").mkdir(parents=True, exist_ok=True, mode=0o700)
    write_json(
        profile / "dpe-updater/mods.json",
        {"setupComplete": not pending, "onboardingPending": pending},
    )
    return {**result, "setupComplete": not pending, "onboardingPending": pending}


def finish_choice(profile: Path, channel: str) -> dict:
    # Welcome's host completes setup only when the user finishes onboarding.
    return (
        status(profile, channel)
        if preferences(profile).get("onboardingPending")
        else complete(profile, channel)
    )


def installer(profile: Path, choice: str) -> Path:
    url, expected = INSTALLERS[choice]
    directory = profile / "dpe-updater/mod-installers"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = directory / expected
    if target.is_file() and not target.is_symlink() and checksum(target) == expected:
        target.chmod(0o700)
        return target
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        download(
            url,
            temporary,
            limit=32 * 1024 * 1024,
            label=f"Downloading {choice.capitalize()} installer…",
        )
        if checksum(temporary) != expected:
            raise ValueError("The mod installer did not pass verification. Try again later.")
        temporary.chmod(0o700)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def replace_file(target: Path, contents: bytes):
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)


def payload_target(generation: Path, choice: str) -> Path:
    return generation / ("dist/patcher.js" if choice == "vencord" else "equicord.asar")


def payload_hashes(generation: Path, choice: str) -> dict:
    """Validate the whole private bundle without loading any JavaScript."""
    if generation.is_symlink() or not generation.is_dir():
        raise ValueError("Invalid mod payload directory")
    files = {}
    total = 0
    for filename in generation.rglob("*"):
        if filename.is_symlink():
            raise ValueError("A mod payload contains a symbolic link")
        if filename.is_dir():
            continue
        if not filename.is_file():
            raise ValueError("A mod payload contains a non-file entry")
        size = filename.stat().st_size
        total += size
        if not size or total > PAYLOAD_LIMIT or len(files) >= 64:
            raise ValueError("A mod payload is incomplete or exceeds its size limit")
        files[filename.relative_to(generation).as_posix()] = checksum(filename)
    target = payload_target(generation, choice)
    if choice == "vencord":
        if not {"dist/" + name for name in REQUIRED_FILES} <= files.keys():
            raise ValueError("The Vencord download is incomplete")
    else:
        # A valid banner alone does not prove that the rest of an ASAR arrived.
        with target.open("rb") as stream:
            size, header, payload, length = struct.unpack("<4I", stream.read(16))
            if (
                size != 4
                or header != payload + 4
                or header % 4
                or not 0 < length <= payload - 4 <= 256 * 1024
            ):
                raise ValueError("Invalid Equicord archive")
            entries = json.loads(stream.read(length))["files"]
        if not REQUIRED_FILES <= entries.keys():
            raise ValueError("The Equicord download is incomplete")
        for entry in entries.values():
            offset, length = int(entry["offset"]), entry["size"]
            if (
                "link" in entry
                or entry.get("unpacked")
                or type(length) is not int
                or offset < 0
                or length <= 0
                or 8 + header + offset + length > target.stat().st_size
            ):
                raise ValueError("The Equicord archive is incomplete")
    return files


def cached_payload(directory: Path, choice: str):
    """Reuse only recently installed bytes, never undo a mod's own self-update."""
    try:
        filename = directory / "mod-payloads" / (choice + ".json")
        if filename.parent.is_symlink() or filename.is_symlink() or filename.stat().st_size > 32768:
            return None
        data = read_object(filename)
        age = time.time() - data["created"]
        if not 0 <= age < PAYLOAD_CACHE_SECONDS or data["installer"] != INSTALLERS[choice][1]:
            return None
        name = data["generation"]
        if not isinstance(name, str) or not re.fullmatch(choice + r"-[a-z0-9_]+", name):
            return None
        generation = filename.parent / name
        if data["files"] != payload_hashes(generation, choice):
            return None
        mod = detect(generation / "loader")
        if (
            mod
            and mod.name.lower() == choice
            and Path(mod.target) == payload_target(generation, choice)
        ):
            return mod
    except (OSError, ValueError, KeyError, TypeError, AttributeError, struct.error):
        pass
    return None


def cache_payload(directory: Path, generation: Path, choice: str, mod):
    (generation / "loader").mkdir()
    (generation / "loader/app.asar").write_bytes(mod.loader)
    write_json(
        directory / "mod-payloads" / (choice + ".json"),
        {
            "created": time.time(),
            "installer": INSTALLERS[choice][1],
            "generation": generation.name,
            "files": payload_hashes(generation, choice),
        },
    )


def installer_cooldown(directory: Path):
    try:
        until = read_object(directory / "mod-installers/cooldown.json")["until"]
        if type(until) in (int, float) and time.time() < until <= time.time() + 60:
            raise RateLimited(until)
    except (OSError, ValueError, KeyError, TypeError) as error:
        if isinstance(error, RateLimited):
            raise


def installer_failure(directory: Path, error=None, output=b"") -> ValueError:
    """Describe failures without surfacing arbitrary installer output or local paths."""
    if error is not None:
        output = getattr(error, "output", b"") or b""
    if isinstance(output, bytes):
        output = output[-16384:].decode("utf-8", "replace")
    output = str(output)[-16384:].lower()
    if re.search(r"rate.?limit|\b429\b|403 forbidden|status code 403", output):
        until = time.time() + 60
        try:
            folder = directory / "mod-installers"
            folder.mkdir(exist_ok=True, mode=0o700)
            write_json(folder / "cooldown.json", {"until": until})
        except OSError:
            pass
        reason = "The mod download service is temporarily limited. Wait a minute, then try again."
    elif isinstance(error, subprocess.TimeoutExpired):
        reason = (
            "The official installer timed out after two minutes. Check your connection and retry."
        )
    elif "no space left" in output or isinstance(error, OSError) and error.errno == 28:
        reason = "There is not enough free space to install the mod. Free some space and retry."
    elif "permission denied" in output or isinstance(error, PermissionError):
        reason = "The official installer could not write its files. Check the profile permissions."
    elif any(word in output for word in ("download", "fetch", "connection", "timeout", "eof")):
        reason = "The mod download did not finish. Check your connection and try again."
    elif isinstance(error, subprocess.CalledProcessError):
        reason = f"The official installer exited with code {error.returncode}. Try again later."
    else:
        reason = "The installer did not produce a working mod. Try again later."
    return ValueError(reason + " Your current mod is unchanged.")


def run_installer(executable: Path, staged: Path, environment: dict) -> bytes:
    # Keep process output out of both the renderer protocol and Python's memory.
    # Only a bounded tail is needed to identify actionable failure categories.
    with tempfile.TemporaryFile() as transcript:

        def tail():
            transcript.seek(0, os.SEEK_END)
            transcript.seek(max(0, transcript.tell() - 16384))
            return transcript.read(16384)

        try:
            result = subprocess.run(
                [str(executable), "-install", "-location", str(staged)],
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=transcript,
                stderr=subprocess.STDOUT,
                timeout=120,
                check=True,
            )
        except subprocess.SubprocessError as error:
            error.output = tail() or getattr(error, "output", b"")
            raise
        output = getattr(result, "stdout", b"")
        return tail() or (output[-16384:] if isinstance(output, bytes) else b"")


def prepare_payload(profile: Path, current: Path, original: bytes, choice: str, notify):
    directory = profile / "dpe-updater"
    cached = cached_payload(directory, choice)
    if cached:
        notify({"stage": "verify", "label": "Using the recently verified mod download…"})
        return cached, None
    if os.environ.get("DPE_CLIENT_OFFLINE") == "1":
        raise ValueError(
            "Mod installation needs an internet connection. Restart Discord online to continue."
        )
    installer_cooldown(directory)
    notify({"stage": "download", "label": "Downloading the official installer…"})
    executable = installer(profile, choice)
    payloads = directory / "mod-payloads"
    payloads.mkdir(exist_ok=True, mode=0o700)
    if payloads.is_symlink():
        raise ValueError("The mod payload directory must be a local directory.")
    # The official wrapper embeds an absolute path. Keep this unique directory
    # after activation instead of moving it or rewriting the official loader.
    generation = Path(tempfile.mkdtemp(prefix=choice + "-", dir=payloads))
    try:
        with tempfile.TemporaryDirectory(prefix="mod-install-", dir=directory) as temporary:
            staged = Path(temporary) / "discord-canary"
            staged_resources = staged / "resources"
            staged_resources.mkdir(parents=True)
            (staged_resources / "app.asar").write_bytes(original)
            shutil.copy2(current / "build_info.json", staged_resources / "build_info.json")
            environment = {
                **os.environ,
                "VENCORD_USER_DATA_DIR": str(generation),
                "EQUICORD_USER_DATA_DIR": str(generation),
                "EQUICORD_DIRECTORY": str(generation / "equicord.asar"),
            }
            for key in ("VENCORD_DEV_INSTALL", "EQUICORD_DEV_INSTALL"):
                environment.pop(key, None)
            notify({"stage": "install", "label": "Installing " + choice.capitalize() + "…"})
            try:
                output = run_installer(executable, staged, environment)
            except (subprocess.SubprocessError, OSError) as error:
                raise installer_failure(directory, error) from error
            notify({"stage": "verify", "label": "Verifying the installation…"})
            installed = detect(staged_resources)
            if (
                installed is None
                or installed.name.lower() != choice
                or Path(installed.target) != payload_target(generation, choice)
            ):
                raise installer_failure(directory, output=output)
            try:
                payload_hashes(generation, choice)
                if installed_version(installed) is None:
                    raise ValueError("The mod build has no valid version")
            except (
                OSError,
                ValueError,
                KeyError,
                TypeError,
                AttributeError,
                struct.error,
            ) as error:
                raise installer_failure(directory, output=output) from error
            staged_backup = staged_resources / "_app.asar"
            if (
                staged_backup.is_symlink()
                or not staged_backup.is_file()
                or staged_backup.read_bytes() != original
            ):
                raise ValueError(
                    "The installer changed the Discord backup. Your current mod is unchanged."
                )
            cache_payload(directory, generation, choice, installed)
            return installed, generation
    except BaseException:
        shutil.rmtree(generation)
        raise


def apply(profile: Path, channel: str, choice: str, notify=lambda _label: None) -> dict:
    if choice not in CHOICES:
        raise ValueError("Choose Vencord, Equicord, or no mod.")
    directory = profile / "dpe-updater"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / "installation.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError(
                "Discord is updating. Wait for it to finish, then try again."
            ) from error
        current = resources(profile, channel)
        existing = detect(current)
        backup = current / "_app.asar"
        if (
            existing is None
            and backup.is_file()
            and not backup.is_symlink()
            and backup.read_bytes() == (current / "app.asar").read_bytes()
        ):
            # Recover an interrupted backup-first install or restore-first removal.
            # Only an exact duplicate of the running bootstrap is disposable.
            backup.unlink()
        if (existing.name.lower() if existing else "none") == choice:
            return finish_choice(profile, channel)
        if existing:
            if backup.is_symlink() or not backup.is_file() or detect_backup(backup):
                raise ValueError(
                    "The original Discord files are missing. Restart Discord to repair them."
                )
            original = backup.read_bytes()
        else:
            # Unknown wrappers must not be mistaken for a healthy project bootstrap.
            if backup.exists() or backup.is_symlink():
                raise ValueError(
                    "An unrecognized mod backup exists. Use its original installer to remove it first."
                )
            original = (current / "app.asar").read_bytes()
        if choice == "none":
            notify({"stage": "remove", "label": "Removing the mod…"})
            replace_file(current / "app.asar", original)
            backup.unlink(missing_ok=True)
            return finish_choice(profile, channel)
        installed, generation = prepare_payload(profile, current, original, choice, notify)
        try:
            # All network work and validation precede this atomic loader swap.
            # The backup is published first: interruption leaves a working old app.
            notify({"stage": "activate", "label": "Applying the mod…"})
            replace_file(backup, original)
            replace_file(current / "app.asar", installed.loader)
        except BaseException:
            if generation is not None:
                shutil.rmtree(generation)
            raise
        return finish_choice(profile, channel)


def detect_backup(archive: Path) -> bool:
    # A backup itself being a known mod wrapper means it cannot restore Discord.
    with tempfile.TemporaryDirectory(prefix="mod-check-", dir=archive.parent) as temporary:
        target = Path(temporary)
        shutil.copyfile(archive, target / "app.asar")
        return detect(target) is not None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "begin", "complete", "apply"))
    parser.add_argument("profile", type=Path)
    parser.add_argument("channel", choices=("stable", "ptb", "canary"))
    parser.add_argument("choice", nargs="?", choices=sorted(CHOICES))
    args = parser.parse_args()

    def progress(state):
        print(json.dumps({"progress": state}), flush=True)

    try:
        profile = args.profile.resolve()
        if args.action == "apply":
            result = apply(profile, args.channel, args.choice, progress)
        elif args.action == "complete":
            result = complete(profile, args.channel)
        elif args.action == "begin":
            result = complete(profile, args.channel, pending=True)
        else:
            result = status(profile, args.channel)
        print(json.dumps({"result": result}), flush=True)
    except Exception as error:
        print(json.dumps({"result": {"error": str(error)}}), flush=True)


if __name__ == "__main__":
    main()
