"""Check a system package as an ordinary user in a disposable distro container."""

import argparse
import json
import os
import pwd
import subprocess
import tempfile
from pathlib import Path


def check(app: Path, archive: Path | None = None, application_archive: Path | None = None):
    # The container installs packages as root. Drop privileges before opening
    # app files, so root-only modes cannot make a broken package appear to pass.
    if os.geteuid() == 0:
        user = pwd.getpwnam("nobody")
        os.setgroups([])
        os.setgid(user.pw_gid)
        os.setuid(user.pw_uid)
    os.chdir("/tmp")
    desktop = next(app.glob("*.desktop"))
    fields = dict(line.split("=", 1) for line in desktop.read_text().splitlines() if "=" in line)
    identity = desktop.stem
    installed = Path("/usr/share/applications") / desktop.name
    icon = Path("/usr/share/icons/hicolor/256x256/apps") / (fields["Icon"] + ".png")
    if installed.read_bytes() != desktop.read_bytes() or fields["StartupWMClass"] != identity:
        raise ValueError("Installed desktop identity does not match the app")
    if fields["Icon"] != identity or icon.read_bytes() != (app / "discord.png").read_bytes():
        raise ValueError("Installed icon is missing or does not match the desktop identity")
    for path in app.rglob("*"):
        if path.is_file():
            with path.open("rb") as stream:
                stream.read(1)
    if (app / "runtime").exists():
        raise ValueError("Installer package must not contain Electron")
    with tempfile.TemporaryDirectory(prefix="installed-runtime-") as profile:
        environment = {**os.environ, "DPE_RUNTIME_BUNDLED": "1", "PYTHONDONTWRITEBYTECODE": "1"}
        bootstrap_command = [
            "python3",
            "-I",
            str(app / "loader.py"),
            "--profile",
            profile,
            "--resolve",
        ]
        if application_archive:
            bootstrap_command += ["--archive", str(application_archive)]
        payload = Path(
            subprocess.check_output(bootstrap_command, env=environment, text=True).strip()
        )
        cached_payload = subprocess.check_output(
            bootstrap_command[:6], env={**environment, "DPE_CLIENT_OFFLINE": "1"}, text=True
        ).strip()
        if cached_payload != str(payload):
            raise ValueError("Bootstrap did not reuse its verified application offline")
        command = [
            "python3",
            str(payload / "integration/updater/runtime.py"),
            str(payload),
            profile,
        ]
        if archive:
            command += ["--archive", str(archive), "--offline"]
        runtime = subprocess.check_output(command, env=environment, text=True).strip()
        # Exercise the same cache again, with network disabled and no archive.
        cached = subprocess.check_output(
            command[:4] + ["--offline"],
            env=environment,
            text=True,
        ).strip()
        if cached != runtime:
            raise ValueError("Installed updater did not reuse its verified runtime")
        libraries = subprocess.check_output(["ldd", runtime], text=True)
        if "not found" in libraries:
            raise ValueError(libraries)
        versions = json.loads(
            subprocess.check_output(
                [runtime, "-p", "JSON.stringify(process.versions)"],
                env={**os.environ, "ELECTRON_RUN_AS_NODE": "1"},
                text=True,
            )
        )
    print(
        json.dumps(
            {
                "passed": True,
                "uid": os.getuid(),
                "versions": versions,
                "files_readable": True,
                "desktop_and_icon_verified": True,
                "runtime_libraries_found": True,
                "bundled_electron": False,
                "cached_runtime_reused_offline": True,
                "gui_verified": False,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument("--runtime-archive", type=Path)
    parser.add_argument("--application-archive", type=Path)
    args = parser.parse_args()
    check(args.app, args.runtime_archive, args.application_archive)
