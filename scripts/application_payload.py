"""Build the updateable application and the small package that trusts it."""

import os
import shutil
import stat
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from common import ROOT, read_json, sha256, write_json
from desktop_identity import profile_name

ASSET = "application.zip"


def bundle(app: Path, archive: Path, version: str, epoch: int) -> dict:
    files = sorted(path for path in app.rglob("*") if path.is_file())
    if any(path.is_symlink() for path in app.rglob("*")):
        raise ValueError("Application payload must not contain links")
    write_json(
        app / "application-index.json", {str(path.relative_to(app)): sha256(path) for path in files}
    )
    timestamp = datetime.fromtimestamp(max(epoch, 315532800), UTC).timetuple()[:6]
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for path in [*files, app / "application-index.json"]:
            item = zipfile.ZipInfo(path.relative_to(app).as_posix(), timestamp)
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = (
                stat.S_IFREG | (0o755 if path.stat().st_mode & 0o111 else 0o644)
            ) << 16
            output.writestr(item, path.read_bytes())
    return {
        "bootstrap_api": 1,
        "asset": ASSET,
        "version": version,
        "archive_sha256": sha256(archive),
        "index_sha256": sha256(app / "application-index.json"),
    }


def bootstrap(source: Path, output: Path, application: dict, tag: str, channel: str, track: str):
    output.mkdir()
    shutil.copy2(ROOT / "integration/bootstrap/loader.py", output / "loader.py")
    shutil.copy2(ROOT / "packaging/release-signing-key.asc", output)
    for path in [source / "discord.png", *source.glob("*.desktop")]:
        shutil.copy2(path, output)
    if (source / "licenses").exists():
        shutil.copytree(source / "licenses", output / "licenses")
    else:
        (output / "licenses").mkdir()
    shutil.copy2(ROOT / "LICENSE", output / "licenses/LICENSE")
    (output / "launch.sh").write_text(
        '#!/bin/sh\nset -eu\nroot=$(dirname -- "$(readlink -f -- "$0")")\n'
        "unset PYTHONHOME PYTHONPATH ELECTRON_RUN_AS_NODE\n"
        'exec python3 -I "$root/loader.py" --launch -- "$@"\n'
    )
    (output / "launch.sh").chmod(0o755)
    write_json(
        output / "bootstrap.json",
        {
            "schema": 1,
            "channel": channel,
            "electron_channel": track,
            "profile": profile_name(channel),
            "application": application,
            "url": f"https://github.com/discord-patched-electron/discord-patched-electron/releases/download/{tag}/{ASSET}",
        },
    )
    (output / "README.txt").write_text(
        "Run ./launch.sh. First launch downloads the verified application and Electron.\n"
        "Approved application, OpenAsar, patch and updater changes install automatically on restart.\n"
        "Your package manager maintains only this bootstrap, desktop entries and system dependencies.\n"
        "See https://github.com/discord-patched-electron/discord-patched-electron/blob/main/docs/install.md\n"
    )
    validate_bootstrap(output)
    for path in output.rglob("*"):
        path.chmod(0o755 if path.is_dir() or path.name == "launch.sh" else 0o644)


def validate_bootstrap(app: Path):
    allowed = {
        "loader.py",
        "launch.sh",
        "bootstrap.json",
        "release-signing-key.asc",
        "discord.png",
        "README.txt",
        "SHA256SUMS",
        "licenses",
    }
    if not {"loader.py", "launch.sh", "bootstrap.json", "release-signing-key.asc"}.issubset(
        {p.name for p in app.iterdir()}
    ):
        raise ValueError("Incomplete bootstrap package")
    for path in app.rglob("*"):
        relative = path.relative_to(app)
        if path.is_symlink() or (
            relative.parts[0] not in allowed
            and not (len(relative.parts) == 1 and path.suffix == ".desktop")
        ):
            raise ValueError(f"Unexpected code in bootstrap package: {relative}")
        if (
            relative.parts[0] == "licenses"
            and path.is_file()
            and (path.suffix in (".py", ".cjs", ".asar", ".so", ".zip") or os.access(path, os.X_OK))
        ):
            raise ValueError("Executable application code cannot be installed as a license")
    policy = read_json(app / "bootstrap.json")
    if policy.get("schema") != 1 or policy["application"].get("asset") != ASSET:
        raise ValueError("Invalid bootstrap download policy")
