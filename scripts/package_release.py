"""Package a tested, relocatable app without compiling Electron.

All four formats contain the same app tree. nFPM creates distro metadata;
no package format downloads or rebuilds upstream code at installation time.
"""

from __future__ import annotations

import argparse
import gzip
import os
import re
import shutil
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path

from channels import require_enabled_discord
from common import (
    ROOT,
    digest,
    package_name,
    read_json,
    require_enabled_electron,
    sha256,
    write_json,
)
from desktop_identity import arch_conflicts, desktop_id
from elftools.elf.elffile import ELFFile
from fetch_reference import validate_capacity
from stage_updater import validate_distribution

REPOSITORY = "https://github.com/discord-patched-electron/discord-patched-electron"


def required_glibc(app: Path) -> str:
    versions = {(2, 35)}
    for path in app.rglob("*"):
        if not path.is_file():
            continue
        with path.open("rb") as stream:
            if stream.read(4) != b"\x7fELF":
                continue
            stream.seek(0)
            section = ELFFile(stream).get_section_by_name(".gnu.version_r")
            if section is not None:
                for _, symbols in section.iter_versions():
                    for symbol in symbols:
                        if re.fullmatch(r"GLIBC_[0-9.]+", symbol.name):
                            versions.add(tuple(map(int, symbol.name[6:].split("."))))
    return ".".join(map(str, max(versions)))


def metadata(
    app: Path, version: str, channel: str, epoch: int, electron: str = "prerelease"
) -> dict:
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", version):
        raise ValueError("Use a numeric release version, such as 20261024.120000")
    require_enabled_discord(channel)
    require_enabled_electron(electron)
    name = package_name(channel, electron)
    root = f"/opt/{name}"
    return {
        "name": name,
        "arch": "amd64",
        "platform": "linux",
        "version": version,
        "version_schema": "none",
        "release": "1",
        "provides": [desktop_id(channel)],
        "conflicts": [desktop_id(channel)],
        "maintainer": "discord-patched-electron contributors <he3als@users.noreply.github.com>",
        "homepage": REPOSITORY,
        "license": "MIT AND BSD-3-Clause AND AGPL-3.0-only",
        "description": f"Discord {channel.title()} with patched Electron for better Linux support\n"
        "Requires an x86-64-v3 (AVX2) CPU. Includes native streaming, Krisp and Linux audio fixes.",
        "mtime": datetime.fromtimestamp(epoch, UTC).isoformat(),
        "contents": [
            {"src": str(app), "dst": root, "type": "tree"},
            {"src": f"{root}/launch.sh", "dst": f"/usr/bin/{name}", "type": "symlink"},
            {
                "src": f"{root}/launch.sh",
                "dst": f"/usr/bin/{desktop_id(channel)}",
                "type": "symlink",
            },
            {
                "src": str(app / (desktop_id(channel) + ".desktop")),
                "dst": f"/usr/share/applications/{desktop_id(channel)}.desktop",
            },
            {
                "src": str(app / "discord.png"),
                "dst": f"/usr/share/icons/hicolor/256x256/apps/{desktop_id(channel)}.png",
            },
        ],
        "overrides": {
            "archlinux": {
                "conflicts": arch_conflicts(channel),
                "depends": [
                    "glibc>=2.35",
                    "gcc-libs",
                    "alsa-lib",
                    "gtk3",
                    "libcups",
                    "libxss",
                    "libxtst",
                    "nss",
                    "libdrm",
                    "mesa",
                    "libpulse",
                    "python>=3.11",
                    "ca-certificates",
                    "gnupg",
                    "brotli",
                    "bash",
                    "coreutils",
                    "pipewire",
                    "pipewire-audio",
                    "zenity",
                ],
                "recommends": [
                    "xdg-desktop-portal: desktop screen selection",
                    "wireplumber: PipeWire session management",
                    "kdotool: KDE Plasma window labels",
                ],
            },
            "deb": {
                "depends": [
                    "libc6 (>= 2.35)",
                    "libstdc++6",
                    "libatomic1",
                    "libasound2t64 | libasound2",
                    "libgtk-3-0t64 | libgtk-3-0",
                    "libnss3",
                    "libcups2t64 | libcups2",
                    "libxss1",
                    "libxtst6",
                    "libgbm1",
                    "libdrm2",
                    "libpulse0",
                    "python3 (>= 3.11)",
                    "ca-certificates",
                    "gpg",
                    "gpgv",
                    "apparmor",
                    "brotli",
                    "bash",
                    "coreutils",
                    "pipewire-bin",
                    "pulseaudio-utils",
                    "zenity",
                ],
                "recommends": ["wireplumber", "xdg-desktop-portal"],
            },
            "rpm": {
                "depends": [
                    "glibc >= 2.35",
                    "libstdc++",
                    "libatomic",
                    "alsa-lib",
                    "gtk3",
                    "nss",
                    "cups-libs",
                    "libXScrnSaver",
                    "libXtst",
                    "mesa-libgbm",
                    "libdrm",
                    "pulseaudio-libs",
                    "python3 >= 3.11",
                    "ca-certificates",
                    "gnupg2",
                    "brotli",
                    "bash",
                    "coreutils",
                    "pipewire-utils",
                    "pulseaudio-utils",
                    "zenity",
                ],
                "recommends": ["wireplumber", "xdg-desktop-portal"],
            },
        },
        "deb": {"compression": "gzip"},
        "rpm": {"compression": "gzip"},
        "archlinux": {"packager": "discord-patched-electron contributors"},
    }


def normalize_permissions(app: Path):
    app.chmod(0o755)
    for path in app.rglob("*"):
        if path.is_symlink():
            if not path.resolve().is_relative_to(app.resolve()):
                raise ValueError(f"Symlink leaves the release tree: {path}")
            continue
        path.chmod(0o755 if path.is_dir() or path.stat().st_mode & 0o111 else 0o644)


def tarball(app: Path, destination: Path, name: str, epoch: int):
    def normalize(entry: tarfile.TarInfo):
        entry.uid = entry.gid = 0
        entry.uname = entry.gname = "root"
        entry.mtime = epoch
        # A portable archive must never install a setuid binary on extraction.
        entry.mode &= 0o777
        return entry

    with (
        destination.open("wb") as raw,
        gzip.GzipFile(
            filename="", fileobj=raw, mode="wb", mtime=epoch, compresslevel=6
        ) as compressed,
        tarfile.open(fileobj=compressed, mode="w|") as archive,
    ):
        archive.add(app, arcname=name, filter=normalize)


def package(app: Path, output: Path, version: str, epoch: int, nfpm: str):
    app, output = app.resolve(), output.resolve()
    build = read_json(app / "local-build.json")
    if not build.get("portable") or build.get("runtime_kind") != "compiled":
        raise ValueError("Only compiled portable builds can be released")
    if not build.get("launch_verified"):
        raise ValueError("Run check_desktop_launch.py on the relocated app before packaging")
    if (app / "resources/_app.asar").exists():
        raise ValueError("Uninstall client mods before packaging the app")
    if build.get("client_pins_verified") is not True:
        raise ValueError("Validate the package's pinned Discord modules before release")
    if sha256(app / "client-update.json") != build.get("client_manifest_sha256"):
        raise ValueError("Pinned Discord module manifest changed after launch validation")
    validate_distribution(app)
    validate_capacity(build["capacity"], True)
    if sha256(app / "runtime/electron") != build["executable_sha256"]:
        raise ValueError("Runtime changed after assembly")
    if output.exists():
        raise ValueError("Choose a fresh package output directory")
    policy = read_json(app / "runtime-update.json")
    if (
        policy["channel"] != build["channel"]
        or policy["electron_version"] != build["capacity"]["compiled_version"]
        or policy["runtime_key"] != build["capacity"]["runtime_key"]
        or policy["archive_sha256"] != build["capacity"]["archive_sha256"]
    ):
        raise ValueError("Runtime update policy differs from the packaged compiler artifact")
    policy["release_version"] = version
    write_json(app / "runtime-update.json", policy)
    normalize_permissions(app)
    config = metadata(app, version, build["channel"], epoch, build["capacity"]["source"]["target"])
    libc = required_glibc(app)
    config["overrides"]["deb"]["depends"][0] = f"libc6 (>= {libc})"
    config["overrides"]["rpm"]["depends"][0] = f"glibc >= {libc}"
    config["overrides"]["archlinux"]["depends"][0] = f"glibc>={libc}"
    output.mkdir(parents=True)
    name = config["name"]
    shutil.copyfile(ROOT / "packaging/linux/refresh-mod-backup.sh", app / "refresh-mod-backup.sh")
    (app / "refresh-mod-backup.sh").chmod(0o644)
    refresh = output / "refresh-mod-backup.sh"
    refresh.write_text(f"#!/bin/sh\nsh /opt/{name}/refresh-mod-backup.sh /opt/{name}\n")
    config["scripts"] = {"postinstall": str(refresh)}
    config["archlinux"]["scripts"] = {"postupgrade": str(refresh)}
    # AppArmor's user-namespace rule is needed on Ubuntu 24.04 and newer.
    # It applies only to this installed executable, never to all Chromium apps.
    profile = output / "apparmor-profile"
    profile.write_text(
        f"abi <abi/4.0>,\ninclude <tunables/global>\n"
        f"profile {name} /opt/{name}/runtime/electron flags=(unconfined) {{\n  userns,\n}}\n"
    )
    postinstall = output / "postinstall.sh"
    postinstall.write_text(
        refresh.read_text()
        + (ROOT / "packaging/linux/postinstall.sh").read_text().replace("@PKGNAME@", name)
    )
    config["contents"].append(
        {"src": str(profile), "dst": f"/etc/apparmor.d/{name}", "type": "config", "packager": "deb"}
    )
    config["overrides"]["deb"]["scripts"] = {"postinstall": str(postinstall)}
    write_json(output / "nfpm.json", config)
    files = sorted(path for path in app.rglob("*") if path.is_file() and path.name != "SHA256SUMS")
    (app / "SHA256SUMS").write_text(
        "".join(f"{sha256(path)}  {path.relative_to(app)}\n" for path in files)
    )
    archive = output / f"{name}-linux-x86_64.tar.gz"
    tarball(app, archive, name, epoch)
    environment = {**os.environ, "SOURCE_DATE_EPOCH": str(epoch)}
    for kind, suffix in (("deb", "deb"), ("rpm", "rpm"), ("archlinux", "pkg.tar.zst")):
        subprocess.run(
            [
                nfpm,
                "package",
                "--config",
                str(output / "nfpm.json"),
                "--packager",
                kind,
                "--target",
                str(output / f"{name}-x86_64.{suffix}"),
            ],
            check=True,
            env=environment,
        )
    assets = [archive, *output.glob("*.deb"), *output.glob("*.rpm"), *output.glob("*.pkg.tar.zst")]
    write_json(
        output / "release.json",
        {
            "schema": 1,
            "signing_required": True,
            "tag_schema": 3,
            "version": version,
            "channel": build["channel"],
            "desktop_id": desktop_id(build["channel"]),
            "client_version": build["client_version"],
            "client": read_json(app / "client-update.json"),
            "client_distribution": "module-only",
            "bundled_discord_client": False,
            "electron_version": build["capacity"]["compiled_version"],
            "runtime_key": build["capacity"]["runtime_key"],
            "runtime": {
                "run": build.get("runtime_provenance", {})
                .get("compiler_run", "")
                .rsplit("/", 1)[-1],
                "compiler_commit": build.get("runtime_provenance", {}).get("compiler_commit"),
                "capacity_sha256": digest(build["capacity"]),
                "archive_sha256": build["capacity"]["archive_sha256"],
                "runtime_key": build["capacity"]["runtime_key"],
                "version": build["capacity"]["compiled_version"],
                "cache_release": "runtime-cache-" + build["capacity"]["runtime_key"],
            },
            "workflow_run": os.environ.get("GITHUB_RUN_ID"),
            "clients_state": os.environ.get("CLIENTS_STATE"),
            "release_key": os.environ.get("RELEASE_KEY"),
            "minimum_cpu": "x86-64-v3",
            "minimum_glibc": libc,
            "source_dirty": bool(
                subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True)
            ),
            "source_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "assets": {path.name: sha256(path) for path in assets},
        },
    )
    assets.append(output / "release.json")
    (output / "SHA256SUMS").write_text("".join(f"{sha256(path)}  {path.name}\n" for path in assets))
    return assets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument(
        "--epoch", type=int, required=True, help="SOURCE_DATE_EPOCH for archive timestamps"
    )
    parser.add_argument("--nfpm", default="nfpm")
    args = parser.parse_args()
    package(args.app, args.output, args.version, args.epoch, args.nfpm)


if __name__ == "__main__":
    main()
