"""Prepare a managed portable dev client's modules and editable helper links.

Executed in the staged app's Python environment, never in an installed package.
"""

import argparse
import fcntl
import sys
from pathlib import Path


def link_helpers(app: Path, voice: Path, sidecars: dict):
    # Node resolves symlinks before resolving relative require() calls. Keep
    # the packaged dpe-* names together, including Python helper siblings.
    audio = app / "integration/audio"
    helpers = {"dpe-app-audio.cjs": audio / "app-audio.cjs"}
    helpers.update({destination: audio / destination for destination in sidecars.values()})
    for destination, source in helpers.items():
        path = voice / destination
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() and path.resolve() == source.resolve():
            continue
        path.unlink(missing_ok=True)
        path.symlink_to(source.resolve())


def prepare(app: Path, profile: Path, downloads: Path):
    if not (app / "dev-version.json").is_file():
        raise ValueError("Editable overlays require a managed development version")
    sys.path.insert(0, str(app / "integration/updater"))
    import client
    from support import checksum, read_object, write_json

    sys.path.insert(0, str(app / "scripts"))
    from common import download
    from patch_app_audio import SIDECARS

    def fetch(url, target, expected, limit=512 * 1024 * 1024):
        # Keep upstream hash verification; cache only exact pinned archive bytes.
        archive = downloads / (expected + ".tar.br")
        with (downloads / (expected + ".lock")).open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            download(url, archive, expected)
        target.write_bytes(archive.read_bytes())

    client.fetch = fetch
    root = profile / "dpe-updater"
    host = client.ensure_client(root, "canary", executable=app / "runtime/electron")
    voice = (host / "modules/discord_voice").resolve()
    link_helpers(app, voice, SIDECARS)
    marker = read_object(voice / ".complete")
    marker["files"] = client.module_files(voice)
    write_json(voice / ".complete", marker)
    # Confirm that overlay links and the complete generation remain valid.
    client.validate_host(root, "canary", host.name)
    from installation import publish

    publish(app, profile, host, app / "runtime/electron")
    return checksum(app / "runtime/electron")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument("profile", type=Path)
    parser.add_argument("downloads", type=Path)
    args = parser.parse_args()
    prepare(args.app.resolve(), args.profile.resolve(), args.downloads.resolve())
