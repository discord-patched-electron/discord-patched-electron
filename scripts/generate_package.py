"""Generate a binary AUR recipe for one channel pair; does not publish it.

Runtime metadata: version, runtime_key, url and sha256. This command makes no
publication or runtime-validation decision. Generated recipes remain candidates.
"""

from __future__ import annotations

import argparse
import io
import re
import shlex
import tarfile
from pathlib import Path
from urllib.parse import urlparse

from channels import enabled_discord_channels, require_enabled_discord
from common import (
    ROOT,
    arch_version,
    electron_channel,
    enabled_electron_channels,
    package_name,
    read_json,
    require_enabled_electron,
    sha256,
    write_json,
)
from desktop_identity import arch_conflicts, desktop_id, display_name, profile_name
from fingerprints import integration_key
from lock_clients import normalize
from updater_python import sources as python_sources

SUPPORT = [
    "scripts/channels.py",
    "scripts/common.py",
    "scripts/desktop_identity.py",
    "scripts/patch_krisp.py",
    "scripts/patch_app_audio.py",
    "scripts/patch_pulse_audio.py",
    "scripts/camera_scope.py",
    "scripts/client_native.py",
    "scripts/stage_package.py",
    "scripts/application_payload.py",
    "integration/bootstrap/loader.py",
    "scripts/stage_updater.py",
    "scripts/updater_python.py",
    "scripts/stage_openasar.py",
    "uv.lock",
    "packaging/upstream/krisp-patcher.py",
    "packaging/upstream/provenance.json",
    "packaging/release-signing-key.asc",
    "config/openasar.lock.json",
    "packaging/discord-canary.png",
    "THIRD_PARTY.md",
    "LICENSE",
]
SUPPORT += [
    str(path.relative_to(ROOT))
    for name in ("audio", "desktop", "updater", "launcher")
    for path in sorted((ROOT / "integration" / name).rglob("*"))
    if path.is_file() and "__pycache__" not in path.parts
]


def validate_client(client: dict):
    version = client["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Invalid client version")
    host = list(map(int, version.split(".")))
    entries = {
        name: {
            "host_version": host,
            "url": source["url"],
            "package_sha256": source["sha256"],
            **({"module_version": source["module_version"]} if name != "core" else {}),
        }
        for name, source in client["sources"].items()
    }
    checked = normalize(
        client["channel"],
        {
            "full": entries.pop("core"),
            "modules": {name: {"full": entry} for name, entry in entries.items()},
            "required_modules": client["required_modules"],
        },
    )
    if checked != client:
        raise ValueError("Client differs from normalized distribution lock")
    for name, source in client["sources"].items():
        suffix = (
            "full.distro" if name == "core" else f"{name}/{source['module_version']}/full.distro"
        )
        expected = f"https://{client['channel']}.dl2.discordapp.net/distro/app/{client['channel']}/linux/x64/{version}/{suffix}"
        if source["url"] != expected:
            raise ValueError("Distribution URL does not match its module revision")


def write_file(path: Path, data: bytes):
    if not path.exists() or path.read_bytes() != data:
        path.write_bytes(data)


def generate(
    runtime: dict, client: dict, channel: str, revision: int, output: Path, *, camera: dict
) -> dict:
    require_enabled_discord(client["channel"])
    require_enabled_electron(channel)
    name = package_name(client["channel"], channel)
    validate_client(client)
    if electron_channel(runtime["version"]) != channel:
        raise ValueError("Runtime belongs to a different Electron channel")
    if type(revision) is not int or revision < 1:
        raise ValueError("pkgrel must be a positive integer")
    for field in ("sha256", "runtime_key"):
        if not re.fullmatch(r"[a-f0-9]{64}", runtime[field]):
            raise ValueError(f"Invalid runtime {field}")
    parsed = urlparse(runtime["url"])
    if (
        parsed.scheme != "https"
        or parsed.netloc != "github.com"
        or parsed.query
        or parsed.fragment
        or not re.fullmatch(
            r"/discord-patched-electron/discord-patched-electron/releases/download/[A-Za-z0-9._+-]+/[A-Za-z0-9._+-]+\.zip",
            parsed.path,
        )
        or "/latest/" in parsed.path
    ):
        raise ValueError("Runtime URL must identify a fixed repository release asset")
    checksum = camera.get("sha256", "")
    expected_url = f"https://github.com/discord-patched-electron/discord-patched-electron/releases/download/camera-{checksum}/libdpe-camera-probe.so"
    if (
        camera.get("schema") != 1
        or not re.fullmatch(r"[a-f0-9]{64}", checksum)
        or camera.get("url") != expected_url
        or camera.get("minimum_cpu") != "x86-64-v3"
    ):
        raise ValueError("Camera helper must identify a checksum-pinned CI binary")
    output.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for source in SUPPORT:
            data = (ROOT / source).read_bytes()
            member = tarfile.TarInfo("support/" + source)
            member.size = len(data)
            member.mode = 0o644
            archive.addfile(member, io.BytesIO(data))
    write_file(output / "support.tar", buffer.getvalue())
    launcher = (
        (ROOT / "integration/launcher/discord-launcher.sh")
        .read_text()
        .replace("@PKGNAME@", name)
        .replace("@DESKTOP_ID@", desktop_id(client["channel"]))
        .replace("@PROFILE_NAME@", profile_name(client["channel"]))
    )
    write_file(output / "discord-launcher.sh", launcher.encode())
    desktop = (
        f"[Desktop Entry]\nName={display_name(client['channel'])}\n"
        f"Exec=/usr/bin/{name} %U\nIcon={desktop_id(client['channel'])}\nType=Application\nTerminal=false\n"
        f"Categories=Network;InstantMessaging;\nStartupWMClass={desktop_id(client['channel'])}\n"
    )
    # No shared x-scheme-handler/discord association: installing a second channel
    # must not silently change which client receives the user's Discord links.
    write_file(output / "discord.desktop", desktop.encode())
    inputs = {
        "schema": 1,
        "package": name,
        "electron_channel": channel,
        "runtime": runtime,
        "client": client,
        "camera_support": camera,
        "integration_key": integration_key(runtime["runtime_key"], client, channel, camera),
    }
    write_json(output / "package-inputs.json", inputs)
    sources = [("libdpe-camera-probe.so::" + camera["url"], checksum)]
    bootstrap = read_json(ROOT / "config/openasar.lock.json")
    sources += [("openasar.tar.gz::" + bootstrap["url"], bootstrap["sha256"])]
    editor = read_json(ROOT / "integration/updater/openasar/source.json")["archive"]
    sources += [("openasar-cdn.tar.gz::" + editor["url"], editor["sha256"])]
    sources += [(item["url"], item["sha256"]) for item in python_sources().values()]
    archives = [
        source.split("::")[0] if "::" in source else Path(urlparse(source).path).name
        for source, _ in sources
    ]
    sources += [
        (filename, sha256(output / filename))
        for filename in [
            "support.tar",
            "discord-launcher.sh",
            "discord.desktop",
            "package-inputs.json",
        ]
    ]
    archives.append("support.tar")
    replacements = {
        "PACKAGE": name,
        "DESKTOP_ID": desktop_id(client["channel"]),
        "CONFLICTS": " ".join(shlex.quote(name) for name in arch_conflicts(client["channel"])),
        "VERSION": arch_version(runtime["version"]),
        "REVISION": str(revision),
        "DISCORD": client["channel"],
        "ELECTRON": channel,
        "SOURCES": "\n".join("  " + shlex.quote(source) for source, _ in sources),
        "CHECKSUMS": "\n".join("  " + shlex.quote(checksum) for _, checksum in sources),
        "ARCHIVES": "\n".join("  " + shlex.quote(archive) for archive in archives),
    }
    recipe = (ROOT / "packaging/PKGBUILD.in").read_text()
    for token, value in replacements.items():
        recipe = recipe.replace("@" + token + "@", value)
    write_file(output / "PKGBUILD", recipe.encode())
    return inputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runtime", type=Path)
    parser.add_argument(
        "--camera-support", type=Path, required=True, help="CI-built camera-support.json"
    )
    parser.add_argument("--discord", required=True, choices=enabled_discord_channels())
    parser.add_argument("--electron", required=True, choices=enabled_electron_channels())
    parser.add_argument("--pkgrel", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    generate(
        read_json(args.runtime),
        read_json(ROOT / "config/clients.lock.json")["clients"][args.discord],
        args.electron,
        args.pkgrel,
        args.output,
        camera=read_json(args.camera_support),
    )


if __name__ == "__main__":
    main()
