"""Assemble a private, directly runnable test app from pinned client/compiler inputs.

This uses the same bootstrap, Krisp, audio and webcam patches as the AUR package.
The output embeds its absolute path; rebuild it if moved. No installation needed.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shlex
import shutil
import subprocess

from build_camera_support import build as camera_build
from camera_scope import write_scope
from common import ROOT, download, package_name, read_json, sha256, write_json
from desktop_identity import desktop_id
from client_inputs import load_clients, validate_channel
from fetch_reference import extract, validate_capacity
from patch_app_audio import patch as audio_patch
from patch_desktop_core import patch as desktop_patch
from patch_krisp import patch as krisp_patch
from prepare_app import prepare


def build(output: Path, channel: str, artifact: Path | None, cache: Path, clients: Path = ROOT / 'config/clients.lock.json'):
    output = output.resolve()
    if output.exists():
        raise ValueError("Choose a fresh output directory")
    client = load_clients(clients, [channel])[channel]
    validate_channel(channel, client)
    capacity = None
    if artifact:
        artifact = artifact.resolve()
        provenance = read_json(artifact / "artifact-provenance.json")
        capacity = provenance["capacity"]
        candidate = capacity["source"]["target"] != "reference"
        validate_capacity(capacity, candidate)
    output.mkdir(parents=True)
    cache.mkdir(parents=True, exist_ok=True)

    def fetch(item):
        name, source = item
        archive = download(source["url"], cache / (name + ".tar.br"), source["sha256"])
        target = output / "core" if name == "core" else output / "modules" / name
        target.mkdir(parents=True)
        subprocess.run(["tar", "-xf", str(archive), "--use-compress-program=brotli",
            "--strip-components=1", "-C", str(target), "files/"], check=True)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(fetch, client["sources"].items()))
    core = output / "core"
    info = read_json(core / "resources/build_info.json")
    if info["version"] != client["version"]:
        raise ValueError("Client host version differs from its lock")
    prepare(core, channel, "prerelease", str(ROOT / "node_modules/.bin/asar"), install_root=output)
    shutil.move(core / "resources", output / "resources")
    if capacity:
        archive = artifact / "binary" / ("runtime-dist.zip" if candidate else "reference-dist.zip")
        extract(archive, output / "runtime", capacity["archive_sha256"])
    else:
        # This explicitly labelled baseline exercises packaging while C++ builds.
        # It never qualifies as a compiled prerelease or a releasable artifact.
        shutil.copytree(core, output / "runtime")
        executable = {"stable":"Discord", "ptb":"DiscordPTB", "canary":"DiscordCanary"}[channel]
        (output / "runtime" / executable).rename(output / "runtime/electron")
    shutil.rmtree(core)
    # Bundled baselines require the packaged ASAR. A custom runtime must keep
    # default_app.asar: adding app.asar makes Electron ignore the CLI entry point
    # (including the optional Equicord loader).
    if not capacity:
        shutil.copytree(output / "resources", output / "runtime/resources", dirs_exist_ok=True)
    krisp = output / "modules/discord_krisp"
    write_json(output / "krisp-patch.json", krisp_patch(krisp / "discord_krisp.node"))
    (krisp / "KMS/logs").mkdir(parents=True, exist_ok=True)
    write_json(output / "app-audio-patch.json", audio_patch(output / "modules/discord_voice", ROOT / "integration/audio/app-audio.cjs"))
    write_json(output / "desktop-patch.json", desktop_patch(output / "modules/discord_desktop_core",
        ROOT / "integration/desktop/safe-storage.cjs", str(ROOT / "node_modules/.bin/asar")))
    write_scope(output / "modules/discord_voice/discord_voice.node", output)
    camera = camera_build(output / "camera-support")
    shutil.copy2(output / "camera-support/libdpe-camera-probe.so", output)
    name = package_name(channel, "prerelease")
    launcher = (ROOT / "integration/launcher/discord-launcher.sh").read_text().replace("@PKGNAME@", name).replace('@DESKTOP_ID@', desktop_id(channel))
    launcher = launcher.replace("root=/usr/lib/" + name, "root=" + shlex.quote(str(output)))
    launcher = launcher.replace('"$config_root/$name"', '"$config_root/$name-local"')
    # A user-owned directory cannot supply a root-owned setuid helper. Chromium
    # still uses its user-namespace sandbox; never disable the whole sandbox.
    launcher = launcher.replace('"$entry" "${flags[@]}"', '"$entry" --disable-setuid-sandbox "${flags[@]}"')
    shutil.copy2(ROOT / 'integration/launcher/equicord.py', output)
    entry = output / "launch.sh"
    entry.write_text(launcher)
    entry.chmod(0o755)
    (output / (desktop_id(channel) + '.desktop')).write_text(
        f'[Desktop Entry]\nType=Application\nName=Discord {channel.title()} (Patched Electron)\n'
        f'Exec="{entry}" %U\nTerminal=false\nCategories=Network;InstantMessaging;\nStartupWMClass=discord\n')
    write_json(output / "local-build.json", {"schema":1, "channel":channel,
        "client_version":client["version"], "runtime_kind":"compiled" if capacity else "bundled-baseline",
        "capacity":capacity, "camera_support":camera, "executable_sha256":sha256(output / "runtime/electron"),
        "launch_verified":False, "publishable":False})
    (output / "README.txt").write_text(
        f"Discord {channel} local test build ({'compiled runtime' if capacity else 'BUNDLED BASELINE ONLY'})\n\n"
        f"Run: {entry}\n\nUses a separate {name}-local profile. Keep this folder at its current path.\n"
        "DPE_APP_AUDIO=0 disables the app-audio adapter; DPE_CAMERA_FAST_PROBE=0 disables the webcam patch.\n"
        "Native Wayland follows your session automatically. Pass --ozone-platform=x11 to test X11.\n")
    print(entry)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--discord", choices=["stable", "ptb", "canary"], default="canary")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--artifact", type=Path, help="Verified fetch_reference.py output directory")
    source.add_argument("--bundled-baseline", action="store_true", help="Packaging test only; not the custom runtime")
    parser.add_argument("--cache", type=Path, required=True, help="Channel-specific locked client archive cache")
    parser.add_argument('--clients', type=Path, default=ROOT / 'config/clients.lock.json')
    args = parser.parse_args()
    build(args.output, args.discord, args.artifact, args.cache.resolve(), args.clients)


if __name__ == "__main__":
    main()
