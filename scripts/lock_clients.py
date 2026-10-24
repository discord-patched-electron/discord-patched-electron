"""Resolve Discord's enabled distribution manifests into reproducible source locks."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from urllib.parse import urlparse

from channels import enabled_discord_channels
from common import DISCORD_CHANNELS, ROOT, download, read_json, write_json


def normalize(channel: str, manifest: dict) -> dict:
    if channel not in DISCORD_CHANNELS:
        raise ValueError("Unknown Discord channel")
    host = manifest["full"]["host_version"]
    if len(host) != 3 or any(type(x) is not int or x < 0 for x in host):
        raise ValueError("Invalid host version")
    version = ".".join(map(str, host))
    entries = {"core": manifest["full"]}
    entries.update({name: module["full"] for name, module in manifest["modules"].items()})
    required = set(manifest.get("required_modules", [])) | {
        "discord_voice",
        "discord_krisp",
        "discord_desktop_core",
    }
    if not required.issubset(entries):
        raise ValueError(f"Missing required modules: {required - entries.keys()}")
    sources = {}
    for name, entry in sorted(entries.items()):
        if name != "core" and not re.fullmatch(r"discord_[a-z0-9_]+", name):
            raise ValueError(f"Invalid module name: {name}")
        if entry["host_version"] != host:
            raise ValueError(f"Mixed host versions in {name}")
        url, checksum = entry["url"], entry["package_sha256"]
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.netloc != f"{channel}.dl2.discordapp.net"
            or not parsed.path.startswith(f"/distro/app/{channel}/linux/x64/{version}/")
            or parsed.query
            or parsed.fragment
            or not re.fullmatch(r"[a-f0-9]{64}", checksum)
        ):
            raise ValueError(f"Unexpected distribution source for {name}")
        sources[name] = {"url": url, "sha256": checksum}
        if name != "core":
            if type(entry["module_version"]) is not int or entry["module_version"] < 0:
                raise ValueError(f"Invalid module version: {name}")
            sources[name]["module_version"] = entry["module_version"]
    return {
        "channel": channel,
        "version": version,
        "sources": sources,
        "required_modules": sorted(required),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "config/clients.lock.json")
    parser.add_argument(
        "--manifest-dir", type=Path, help="Use already-downloaded <channel>-manifest.json files"
    )
    args = parser.parse_args()
    clients = {}
    for channel in enabled_discord_channels():
        source = (args.manifest_dir or ROOT / ".work/manifests") / f"{channel}-manifest.json"
        if args.manifest_dir is None:
            download(
                f"https://updates.discord.com/distributions/app/manifests/latest?channel={channel}&platform=linux&arch=x64",
                source,
            )
        clients[channel] = normalize(channel, read_json(source))
    write_json(args.output, {"schema": 1, "clients": clients})
    for channel, client in clients.items():
        print(f"{channel}: {client['version']}, {len(client['sources']) - 1} modules")


if __name__ == "__main__":
    main()
