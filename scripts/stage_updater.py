"""Stage project-owned resources for the Linux updater package, without client bytes."""

import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

from common import ROOT, download, read_json, sha256, write_json
from desktop_identity import desktop_id
from patch_app_audio import SIDECARS
from stage_openasar import stage as stage_editor
from updater_python import stage as stage_python


def stage(
    output: Path,
    channel: str,
    version: str,
    asar: str,
    source_archive: Path | None = None,
    *,
    client: dict,
    python_archives: Path | None = None,
    cdn_archive: Path | None = None,
):
    if client["channel"] != channel or client["version"] != version:
        raise ValueError("Pinned Discord modules differ from package identity")
    host = list(map(int, version.split(".")))
    module_manifest = {
        "channel": channel,
        "full": {"host_version": host},
        "required_modules": client["required_modules"],
        "modules": {
            name: {
                "full": {
                    "host_version": host,
                    "module_version": source["module_version"],
                    "package_sha256": source["sha256"],
                    "url": source["url"],
                }
            }
            for name, source in client["sources"].items()
            if name != "core"
        },
    }
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "client-update.json", module_manifest)
    lock = read_json(ROOT / "config/openasar.lock.json")
    # Include the exact corresponding source and license for the AGPL bootstrap.
    with tempfile.TemporaryDirectory(prefix="openasar-") as temporary:
        archive = source_archive or download(
            lock["url"], Path(temporary) / "source.tar.gz", lock["sha256"]
        )
        if sha256(archive) != lock["sha256"]:
            raise ValueError("OpenAsar source checksum mismatch")
        with tarfile.open(archive) as source:
            prefix = "OpenAsar-" + lock["commit"] + "/"
            for member in source:
                relative = member.name.removeprefix(prefix)
                if not member.isfile() or not (
                    relative.startswith("src/") or relative == "LICENSE"
                ):
                    continue
                if ".." in Path(relative).parts or relative.startswith("/"):
                    raise ValueError("Unexpected OpenAsar source path")
                destination = output / "bootstrap" / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(source.extractfile(member).read())
    resources = output / "resources"
    resources.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="loader-") as temporary:
        loader = Path(temporary)
        write_json(
            loader / "package.json",
            {
                "name": "discord",
                "version": "1.0.0",
                "license": "MIT",
                "main": "main.cjs",
                "desktopName": desktop_id(channel) + ".desktop",
            },
        )
        (loader / "main.cjs").write_text(
            'require(process.env.DPE_APP_ROOT + "/integration/updater/entry.cjs");\n'
        )
        subprocess.run([asar, "pack", str(loader), str(resources / "app.asar")], check=True)
    # Official mod installers use this to identify the installation. The loader
    # reads the live client metadata from Discord's per-user installation.
    write_json(resources / "build_info.json", {"releaseChannel": channel, "version": version})
    for directory in ("updater", "audio", "desktop"):
        shutil.copytree(
            ROOT / "integration" / directory,
            output / "integration" / directory,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    shutil.copy2(
        ROOT / "packaging/release-signing-key.asc",
        output / "integration/updater/release-signing-key.asc",
    )
    for source, destination in SIDECARS.items():
        shutil.copy2(
            ROOT / "integration/audio" / source, output / "integration/audio" / destination
        )
    scripts = output / "scripts"
    scripts.mkdir()
    for name in (
        "client_native.py",
        "camera_scope.py",
        "patch_krisp.py",
        "patch_app_audio.py",
        "patch_pulse_audio.py",
        "common.py",
    ):
        shutil.copy2(ROOT / "scripts" / name, scripts)
    upstream = output / "packaging/upstream"
    upstream.mkdir(parents=True)
    for name in ("krisp-patcher.py", "provenance.json"):
        shutil.copy2(ROOT / "packaging/upstream" / name, upstream)
    write_json(output / "openasar-source.json", lock)
    shutil.copy2(ROOT / "discord-patched-electron.png", output / "discord.png")
    stage_python(output, python_archives)
    stage_editor(output, cdn_archive)
    write_json(
        output / "client-distribution.json",
        {
            "schema": 1,
            "mode": "module-only",
            "bundled_discord_client": False,
            "loader_sha256": sha256(resources / "app.asar"),
        },
    )
    # Atomic JSON writers default to 0600; system packages must be readable by
    # ordinary users after the package manager installs them as root.
    for filename in (
        "resources/build_info.json",
        "openasar-source.json",
        "client-distribution.json",
        "client-update.json",
    ):
        (output / filename).chmod(0o644)


def configure_runtime(
    output: Path,
    channel: str,
    electron_channel: str,
    version: str,
    runtime_key: str,
    archive_sha256: str,
    url: str | None = None,
):
    write_json(
        output / "runtime-update.json",
        {
            "schema": 1,
            "channel": channel,
            "electron_channel": electron_channel,
            "electron_version": version,
            "runtime_key": runtime_key,
            "archive_sha256": archive_sha256,
            "release_version": None,
            **({"url": url} if url else {}),
        },
    )
    (output / "runtime-update.json").chmod(0o644)


def validate_payload_entry(
    relative: str, *, is_dir: bool, link: str | None = None, allow_runtime: bool = True
):
    """Apply the same client-free layout rules to staging trees and archives."""
    path = Path(relative)
    if not allow_runtime and path.parts and path.parts[0] == "runtime":
        raise ValueError(f"Electron runtime cannot be shipped in the updater package: {relative}")
    if path.name == "__pycache__" or path.suffix == ".pyc":
        raise ValueError(f"Generated Python bytecode cannot be shipped: {relative}")
    if link is not None:
        if relative != "runtime/resources" or link != "../resources":
            raise ValueError(f"Unexpected release symlink: {relative}")
    elif (
        path.name
        in (
            "discord_loading.webm",
            "whitney_400.woff",
            "ggsans-400-normal.woff2",
            "ggsans-500-medium.woff2",
            "connecting.webp",
            "connecting-dark.webm",
            "connecting-light.webm",
        )
        or (
            path.suffix in (".asar", ".node", ".distro", ".br") and relative != "resources/app.asar"
        )
        or path.name in ("modules", "core", "updater_bootstrap", "installer.db")
        or (is_dir and path.name.startswith("app-"))
    ):
        raise ValueError(f"Discord client data cannot be shipped: {relative}")


def validate_distribution(app: Path, *, allow_runtime: bool = True):
    """Fail closed if a staging tree contains an old bundled client or mod loader."""
    policy = read_json(app / "client-distribution.json")
    if policy.get("mode") != "module-only" or policy.get("bundled_discord_client") is not False:
        raise ValueError("Release must use the client-free Linux updater layout")
    if sha256(app / "resources/app.asar") != policy.get("loader_sha256"):
        raise ValueError("Release loader differs from the project-owned bootstrap")
    for path in app.rglob("*"):
        validate_payload_entry(
            path.relative_to(app).as_posix(),
            is_dir=path.is_dir(),
            link=str(path.readlink()) if path.is_symlink() else None,
            allow_runtime=allow_runtime,
        )
