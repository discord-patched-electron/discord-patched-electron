"""Package a tested, relocatable app without compiling Electron.

The tarball, deb and rpm contain the same app tree. nFPM creates distro metadata;
no package format downloads or rebuilds upstream code at installation time.
"""

from __future__ import annotations

import argparse
import gzip
import os
import re
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path

from common import ROOT, read_json, sha256, write_json
from desktop_identity import desktop_id
from elftools.elf.elffile import ELFFile
from fetch_reference import validate_capacity

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


def metadata(app: Path, version: str, channel: str, epoch: int) -> dict:
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", version):
        raise ValueError("Use a numeric release version, such as 20261024.120000")
    if channel not in {"stable", "ptb", "canary"}:
        raise ValueError("Unknown Discord channel")
    name = f"discord-{channel}-prerelease-patched-electron-bin"
    root = f"/opt/{name}"
    return {
        "name": name,
        "arch": "amd64",
        "platform": "linux",
        "version": version,
        "version_schema": "none",
        "maintainer": "discord-patched-electron contributors <he3als@users.noreply.github.com>",
        "homepage": REPOSITORY,
        "license": "LicenseRef-Discord AND MIT AND BSD-3-Clause",
        "description": f"Discord {channel.title()} with patched prerelease Electron\n"
        "Requires an x86-64-v3 (AVX2) CPU. Includes native streaming, Krisp and Linux audio fixes.",
        "mtime": datetime.fromtimestamp(epoch, UTC).isoformat(),
        "contents": [
            {"src": str(app), "dst": root, "type": "tree"},
            {"src": f"{root}/launch.sh", "dst": f"/usr/bin/{name}", "type": "symlink"},
            {
                "src": str(app / (desktop_id(channel) + ".desktop")),
                "dst": f"/usr/share/applications/{desktop_id(channel)}.desktop",
            },
            {"src": str(app / "discord.png"), "dst": f"/usr/share/pixmaps/{name}.png"},
        ],
        "overrides": {
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
                    "python3",
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
                    "python3",
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
    validate_capacity(build["capacity"], True)
    if sha256(app / "runtime/electron") != build["executable_sha256"]:
        raise ValueError("Runtime changed after assembly")
    if output.exists():
        raise ValueError("Choose a fresh package output directory")
    normalize_permissions(app)
    config = metadata(app, version, build["channel"], epoch)
    libc = required_glibc(app)
    config["overrides"]["deb"]["depends"][0] = f"libc6 (>= {libc})"
    config["overrides"]["rpm"]["depends"][0] = f"glibc >= {libc}"
    output.mkdir(parents=True)
    name = config["name"]
    # AppArmor's user-namespace rule is needed on Ubuntu 24.04 and newer.
    # It applies only to this installed executable, never to all Chromium apps.
    profile = output / "apparmor-profile"
    profile.write_text(
        f"abi <abi/4.0>,\ninclude <tunables/global>\n"
        f"profile {name} /opt/{name}/runtime/electron flags=(unconfined) {{\n  userns,\n}}\n"
    )
    postinstall = output / "postinstall.sh"
    postinstall.write_text(
        (ROOT / "packaging/linux/postinstall.sh").read_text().replace("@PKGNAME@", name)
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
    archive = output / f"{name}-{version}-linux-x86_64.tar.gz"
    tarball(app, archive, name, epoch)
    environment = {**os.environ, "SOURCE_DATE_EPOCH": str(epoch)}
    for kind in ("deb", "rpm"):
        subprocess.run(
            [
                nfpm,
                "package",
                "--config",
                str(output / "nfpm.json"),
                "--packager",
                kind,
                "--target",
                str(output / f"{name}-{version}.") + kind,
            ],
            check=True,
            env=environment,
        )
    assets = [archive, *output.glob("*.deb"), *output.glob("*.rpm")]
    write_json(
        output / "release.json",
        {
            "schema": 1,
            "version": version,
            "channel": build["channel"],
            "client_version": build["client_version"],
            "electron_version": build["capacity"]["compiled_version"],
            "runtime_key": build["capacity"]["runtime_key"],
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
