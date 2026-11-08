"""Prepare a managed portable dev client's modules and editable helper links.

Executed in the staged app's Python environment, never in an installed package.
"""

import argparse
import fcntl
import hashlib
import os
import sys
import tempfile
from pathlib import Path


def configure_installation(app: Path):
    installation = app / "integration/updater/installation.py"
    source = installation.read_text()
    before = '"host": str(host),'
    after = (
        before
        + """
                "dev_helper_links": {
                    item.relative_to(host / "modules/discord_voice").as_posix(): os.readlink(item)
                    for item in (host / "modules/discord_voice").rglob("*")
                    if item.is_symlink()
                },"""
    )
    if after in source:
        return
    if source.count(before) != 1:
        raise ValueError("Unsupported development installation identity")
    installation.write_text(source.replace(before, after))


def configure_startup(app: Path):
    """Stage the private dev overlay inside the normal splash updater worker."""
    if not (app / "dev-version.json").is_file():
        raise ValueError("Editable overlays require a managed development version")
    configure_installation(app)
    helper = app / "integration/updater/dev_client.py"
    contents = Path(__file__).read_bytes()
    if not helper.is_file() or helper.read_bytes() != contents:
        # Another profile may be importing this worker; publish a complete file.
        with tempfile.NamedTemporaryFile(dir=helper.parent, delete=False) as output:
            temporary = Path(output.name)
            try:
                output.write(contents)
                output.flush()
                temporary.replace(helper)
            finally:
                temporary.unlink(missing_ok=True)
    check = app / "integration/updater/check.py"
    source = check.read_text()
    before = (
        "    host = ensure_client(client_root, channel, offline=offline, executable=executable)"
    )
    after = """    from dev_client import prepare

    host = prepare(
        app, client_root.parent, Path(os.environ["DPE_DEV_DOWNLOADS"]),
        executable=executable, offline=offline,
    )"""
    published = "        installed = publish(app, client_root.parent, host, executable)"
    shared = (
        published
        + """
        from dev_client import share_installed_runtime

        with (Path(os.environ["DPE_DEV_DOWNLOADS"]).parent / "locks/catalog").open("a") as lock:
            import fcntl

            fcntl.flock(lock, fcntl.LOCK_EX)
            share_installed_runtime(app, client_root.parent)"""
    )
    if after in source and shared in source:
        return
    if source.count(before) != 1 or source.count(published) != 1:
        raise ValueError("Unsupported development startup layout")
    check.write_text(source.replace(before, after).replace(published, shared))


def share_installed_runtime(app: Path, profile: Path):
    def sha256(path):
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    def files(root):
        for directory, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = [name for name in dirs if not (Path(directory) / name).is_symlink()]
            for name in names:
                path = Path(directory) / name
                if not path.is_symlink() and path.is_file():
                    yield path

    # Publishing uses isolated copies for normal installs. In a managed
    # profile only, fold identical immutable Electron files back into the pool.
    trees = list((profile / "dpe-updater/installations").glob("*"))
    trees += list(profile.glob("app-*"))
    for original in files(app / "runtime"):
        if original.stat().st_mode & 0o7222:
            continue
        relative = original.relative_to(app / "runtime")
        for tree in trees:
            candidate = tree / relative
            if candidate.is_symlink() or not candidate.is_file():
                continue
            if candidate.stat().st_ino == original.stat().st_ino:
                continue
            if candidate.stat().st_size != original.stat().st_size:
                continue
            if sha256(candidate) != sha256(original):
                continue
            temporary = candidate.with_name(candidate.name + ".dev-link")
            try:
                os.link(original, temporary)
                temporary.replace(candidate)
            finally:
                temporary.unlink(missing_ok=True)


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


def prepare(
    app: Path,
    profile: Path,
    downloads: Path,
    *,
    executable: Path | None = None,
    offline: bool = False,
):
    if not (app / "dev-version.json").is_file():
        raise ValueError("Editable overlays require a managed development version")
    sys.path.insert(0, str(app / "integration/updater"))
    import client
    from support import read_object, write_json

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
    host = client.ensure_client(
        root, "canary", offline=offline, executable=executable or app / "runtime/electron"
    )
    voice = (host / "modules/discord_voice").resolve()
    link_helpers(app, voice, SIDECARS)
    marker = read_object(voice / ".complete")
    marker["files"] = client.module_files(voice)
    write_json(voice / ".complete", marker)
    # Confirm that overlay links and the complete generation remain valid.
    client.validate_host(root, "canary", host.name)
    return host


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument("profile", type=Path)
    parser.add_argument("downloads", type=Path)
    args = parser.parse_args()
    configure_installation(args.app.resolve())
    host = prepare(args.app.resolve(), args.profile.resolve(), args.downloads.resolve())
    from installation import publish

    publish(
        args.app.resolve(), args.profile.resolve(), host, args.app.resolve() / "runtime/electron"
    )
