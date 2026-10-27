"""Prepare approved application code; the installed bootstrap activates it on restart."""

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from runtime import FAILURES, approved_release, recently_checked
from support import download, progress, read_object, write_json


def prepare(app: Path, profile: Path) -> Path | None:
    package = os.environ.get("DPE_INSTALL_ROOT")
    if (
        not package
        or os.environ.get("DPE_CLIENT_OFFLINE") == "1"
        or os.environ.get("DPE_APPLICATION_UPDATES") == "0"
        or os.environ.get("DPE_CLIENT_PINNED") == "1"
    ):
        return None
    package = Path(package)
    policy = read_object(package / "bootstrap.json")
    # The marker lives beside the immutable app, never inside its indexed files.
    checked = app.parent / "last-check.json"
    if recently_checked(checked):
        return pending(package, profile, app)
    write_json(checked, time.time())
    try:
        with tempfile.TemporaryDirectory(prefix="application-update-", dir=app.parent) as temporary:
            directory = Path(temporary)
            progress("check", "Checking Discord Patched Electron")
            manifest = approved_release(
                directory,
                policy,
                package / "release-signing-key.asc",
                fetch=True,
                application_update=True,
            )
            incoming = manifest.get("application")
            current = read_object(app.parent / "application.json")
            if incoming == current and not (app.parent / "proof").exists():
                # Preserve approval of the initial package-pinned generation so
                # it can remain a rollback target after a later package upgrade.
                proof = directory / "retained"
                proof.mkdir()
                for name in (
                    "release.json",
                    "SHA256SUMS",
                    "SHA256SUMS.sig",
                    "github.json",
                    "approval.json",
                ):
                    shutil.copyfile(directory / name, proof / name)
                proof.rename(app.parent / "proof")
            if not incoming or incoming["version"] <= max(
                current["version"], policy["application"]["version"]
            ):
                return None
            state_path = app.parents[2] / "state.json"
            if state_path.exists() and incoming["archive_sha256"] in read_object(state_path).get(
                "rejected", []
            ):
                return None
            if incoming.get("bootstrap_api") != 1 or incoming.get("asset") != "application.zip":
                raise ValueError(
                    "This update needs a newer bootstrap package; update it with your package manager"
                )
            tag = read_object(directory / "github.json")["tag_name"]
            url = f"https://github.com/discord-patched-electron/discord-patched-electron/releases/download/{tag}/application.zip"
            archive = directory / "application.zip"
            download(url, archive, limit=64 * 1024 * 1024, label="Discord Patched Electron")
            # Keep the archive outside proof so cached approval evidence stays small.
            proof = directory / "proof"
            proof.mkdir()
            for name in (
                "release.json",
                "SHA256SUMS",
                "SHA256SUMS.sig",
                "github.json",
                "approval.json",
            ):
                (directory / name).rename(proof / name)
            progress("install", "Discord Patched Electron")
            result = subprocess.check_output(
                [
                    sys.executable,
                    "-I",
                    str(package / "loader.py"),
                    "--profile",
                    str(profile),
                    "--stage",
                    str(proof),
                    "--archive",
                    str(archive),
                ],
                text=True,
            ).strip()
            return Path(result) if Path(result) != app else None
    except FAILURES as error:
        print(
            f"Application update unavailable; retaining the current version: {error}",
            file=sys.stderr,
        )
        return None


def pending(package: Path, profile: Path, app: Path) -> Path | None:
    try:
        selected = Path(
            subprocess.check_output(
                [
                    sys.executable,
                    "-I",
                    str(package / "loader.py"),
                    "--profile",
                    str(profile),
                    "--resolve",
                ],
                text=True,
                env={**os.environ, "DPE_CLIENT_OFFLINE": "1"},
            ).strip()
        )
        return selected if selected != app else None
    except FAILURES:
        return None
