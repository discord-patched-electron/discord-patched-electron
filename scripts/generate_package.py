"""Generate a binary AUR recipe for one channel pair; does not publish it.

Runtime metadata: version, runtime_key, url and sha256. This command makes no
publication or runtime-validation decision. Generated recipes remain candidates.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import shlex
from urllib.parse import urlparse

from common import ROOT, arch_version, electron_channel, package_name, read_json, sha256, write_json
from fingerprints import integration_key
from lock_clients import normalize

SUPPORT = {
    "common.py": "scripts/common.py",
    "prepare_app.py": "scripts/prepare_app.py",
    "patch_krisp.py": "scripts/patch_krisp.py",
    "patch_app_audio.py": "scripts/patch_app_audio.py",
    "app-audio.cjs": "integration/audio/app-audio.cjs",
    "wayland-audio.cjs": "integration/audio/wayland-audio.cjs",
    "pipewire_audio.py": "integration/audio/pipewire_audio.py",
    "stage_package.py": "scripts/stage_package.py",
    "krisp-patcher.py": "packaging/upstream/krisp-patcher.py",
    "provenance.json": "packaging/upstream/provenance.json",
    "THIRD_PARTY.md": "THIRD_PARTY.md",
}


def validate_client(client: dict):
    version = client["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Invalid client version")
    host = list(map(int, version.split(".")))
    entries = {name: {"host_version": host, "url": source["url"],
                     "package_sha256": source["sha256"], **({"module_version": source["module_version"]} if name != "core" else {})}
               for name, source in client["sources"].items()}
    checked = normalize(client["channel"], {"full": entries.pop("core"),
        "modules": {name: {"full": entry} for name, entry in entries.items()},
        "required_modules": client["required_modules"]})
    if checked != client:
        raise ValueError("Client differs from normalized distribution lock")
    for name, source in client["sources"].items():
        suffix = "full.distro" if name == "core" else f"{name}/{source['module_version']}/full.distro"
        expected = f"https://{client['channel']}.dl2.discordapp.net/distro/app/{client['channel']}/linux/x64/{version}/{suffix}"
        if source["url"] != expected:
            raise ValueError("Distribution URL does not match its module revision")


def write_file(path: Path, data: bytes):
    if not path.exists() or path.read_bytes() != data:
        path.write_bytes(data)


def generate(runtime: dict, client: dict, channel: str, revision: int, output: Path) -> dict:
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
    if (parsed.scheme != "https" or parsed.netloc != "github.com" or parsed.query or parsed.fragment
            or not re.fullmatch(r"/discord-patched-electron/discord-patched-electron/releases/download/[A-Za-z0-9._+-]+/[A-Za-z0-9._+-]+\.zip", parsed.path)
            or "/latest/" in parsed.path):
        raise ValueError("Runtime URL must identify a fixed repository release asset")
    output.mkdir(parents=True, exist_ok=True)
    for filename, source in SUPPORT.items():
        write_file(output / filename, (ROOT / source).read_bytes())
    launcher = (ROOT / "integration/launcher/discord-launcher.sh").read_text().replace("@PKGNAME@", name)
    write_file(output / "discord-launcher.sh", launcher.encode())
    desktop = (f"[Desktop Entry]\nName=Discord {client['channel'].upper()} (Electron {channel})\n"
               f"Exec=/usr/bin/{name} %U\nIcon={name}\nType=Application\nTerminal=false\n"
               f"Categories=Network;InstantMessaging;\nStartupWMClass=discord\n")
    # No shared x-scheme-handler/discord association: installing a second channel
    # must not silently change which client receives the user's Discord links.
    write_file(output / "discord.desktop", desktop.encode())
    inputs = {"schema": 1, "package": name, "electron_channel": channel,
              "runtime": runtime, "client": client,
              "integration_key": integration_key(runtime["runtime_key"], client, channel)}
    write_json(output / "package-inputs.json", inputs)
    sources = [("runtime.zip::" + runtime["url"], runtime["sha256"])]
    sources += [(name + ".tar.br::" + source["url"], source["sha256"])
                for name, source in sorted(client["sources"].items())]
    archives = [source.split("::")[0] for source, _ in sources]
    sources += [(filename, sha256(output / filename)) for filename in
                [*SUPPORT, "discord-launcher.sh", "discord.desktop", "package-inputs.json"]]
    # Keep the upstream recipe's live text licenses; binary inputs never use SKIP.
    sources += [("LICENSE.html::https://discord.com/terms", "SKIP"),
                ("OSS-LICENSES.html::https://discord.com/licenses", "SKIP")]
    replacements = {"PACKAGE": name, "VERSION": arch_version(runtime["version"]),
        "REVISION": str(revision), "DISCORD": client["channel"], "ELECTRON": channel,
        "SOURCES": "\n".join("  " + shlex.quote(source) for source, _ in sources),
        "CHECKSUMS": "\n".join("  " + shlex.quote(checksum) for _, checksum in sources),
        "ARCHIVES": "\n".join("  " + shlex.quote(archive) for archive in archives)}
    recipe = (ROOT / "packaging/PKGBUILD.in").read_text()
    for token, value in replacements.items():
        recipe = recipe.replace("@" + token + "@", value)
    write_file(output / "PKGBUILD", recipe.encode())
    return inputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runtime", type=Path)
    parser.add_argument("--discord", required=True, choices=["stable", "ptb", "canary"])
    parser.add_argument("--electron", required=True, choices=["stable", "prerelease", "nightly"])
    parser.add_argument("--pkgrel", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    generate(read_json(args.runtime), read_json(ROOT / "config/clients.lock.json")["clients"][args.discord],
             args.electron, args.pkgrel, args.output)


if __name__ == "__main__":
    main()
