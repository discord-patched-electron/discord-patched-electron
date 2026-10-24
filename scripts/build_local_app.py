"""Assemble a private, directly runnable test app from pinned client/compiler inputs.

This uses the same bootstrap, Krisp, audio and webcam patches as the AUR package.
Use --portable for a relocatable release tree. No installation needed.
"""

from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from build_camera_support import build as camera_build
from camera_scope import write_scope
from client_inputs import load_clients, validate_channel
from common import ROOT, download, package_name, read_json, sha256, write_json
from desktop_identity import desktop_id
from fetch_reference import extract, validate_capacity
from patch_app_audio import patch as audio_patch
from patch_desktop_core import patch as desktop_patch
from patch_krisp import patch as krisp_patch
from prepare_app import prepare


def build(
    output: Path,
    channel: str,
    artifact: Path | None,
    cache: Path,
    clients: Path = ROOT / "config/clients.lock.json",
    *,
    portable: bool = False,
):
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
    if portable and (not capacity or not candidate):
        raise ValueError("Portable releases require a verified compiled candidate runtime")
    output.mkdir(parents=True)
    cache.mkdir(parents=True, exist_ok=True)

    def fetch(item):
        name, source = item
        archive = download(source["url"], cache / (name + ".tar.br"), source["sha256"])
        target = output / "core" if name == "core" else output / "modules" / name
        target.mkdir(parents=True)
        subprocess.run(
            [
                "tar",
                "-xf",
                str(archive),
                "--use-compress-program=brotli",
                "--strip-components=1",
                "-C",
                str(target),
                "files/",
            ],
            check=True,
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(fetch, client["sources"].items()))
    core = output / "core"
    info = read_json(core / "resources/build_info.json")
    if info["version"] != client["version"]:
        raise ValueError("Client host version differs from its lock")
    prepare(
        core,
        channel,
        "prerelease",
        str(ROOT / "node_modules/.bin/asar"),
        install_root=output,
        portable=portable,
    )
    shutil.move(core / "resources", output / "resources")
    shutil.copy2(core / "discord.png", output)
    if capacity:
        archive = artifact / "binary" / ("runtime-dist.zip" if candidate else "reference-dist.zip")
        extract(archive, output / "runtime", capacity["archive_sha256"])
    else:
        # This explicitly labelled baseline exercises packaging while C++ builds.
        # It never qualifies as a compiled prerelease or a releasable artifact.
        shutil.copytree(core, output / "runtime")
        executable = {"stable": "Discord", "ptb": "DiscordPTB", "canary": "DiscordCanary"}[channel]
        (output / "runtime" / executable).rename(output / "runtime/electron")
    shutil.rmtree(core)
    # Use Electron's normal packaged-app entry point. A directory symlink keeps
    # the official mod installer's resources/app.asar and Electron's lookup in sync.
    runtime_resources = output / "runtime/resources"
    if runtime_resources.exists():
        shutil.rmtree(runtime_resources)
    runtime_resources.symlink_to("../resources", target_is_directory=True)
    krisp = output / "modules/discord_krisp"
    write_json(output / "krisp-patch.json", krisp_patch(krisp / "discord_krisp.node"))
    (krisp / "KMS/logs").mkdir(parents=True, exist_ok=True)
    write_json(
        output / "app-audio-patch.json",
        audio_patch(output / "modules/discord_voice", ROOT / "integration/audio/app-audio.cjs"),
    )
    write_json(
        output / "desktop-patch.json",
        desktop_patch(
            output / "modules/discord_desktop_core",
            ROOT / "integration/desktop/safe-storage.cjs",
            str(ROOT / "node_modules/.bin/asar"),
        ),
    )
    write_scope(output / "modules/discord_voice/discord_voice.node", output)
    camera = camera_build(output / "camera-support")
    shutil.copy2(output / "camera-support/libdpe-camera-probe.so", output)
    name = package_name(channel, "prerelease")
    launcher = (
        (ROOT / "integration/launcher/discord-launcher.sh")
        .read_text()
        .replace("@PKGNAME@", name)
        .replace("@DESKTOP_ID@", desktop_id(channel))
    )
    root_line = (
        'root=$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")'
        if portable
        else "root=" + shlex.quote(str(output))
    )
    launcher = launcher.replace("root=/usr/lib/" + name, root_line)
    if not portable:
        launcher = launcher.replace('"$config_root/$name"', '"$config_root/$name-local"')
    # A user-owned directory cannot supply a root-owned setuid helper. Chromium
    # still uses its user-namespace sandbox; never disable the whole sandbox.
    launcher = launcher.replace(
        '"$root/runtime/electron" "${flags[@]}"',
        '"$root/runtime/electron" --disable-setuid-sandbox "${flags[@]}"',
    )
    entry = output / "launch.sh"
    entry.write_text(launcher)
    entry.chmod(0o755)
    desktop_exec = f"/usr/bin/{name}" if portable else f'"{entry}"'
    (output / (desktop_id(channel) + ".desktop")).write_text(
        f"[Desktop Entry]\nType=Application\nName=Discord {channel.title()} (Patched Electron)\n"
        f"Exec={desktop_exec} %U\nIcon={name}\nTerminal=false\nCategories=Network;InstantMessaging;\nStartupWMClass=discord\n"
    )
    (output / "licenses").mkdir()
    for filename in ("LICENSE", "THIRD_PARTY.md"):
        shutil.copy2(ROOT / filename, output / "licenses")
    if portable:
        for filename, url in (
            ("DISCORD-TERMS.html", "https://discord.com/terms"),
            ("DISCORD-LICENSES.html", "https://discord.com/licenses"),
        ):
            download(url, output / "licenses" / filename)
    write_json(output / "client-inputs.json", client)
    write_json(
        output / "local-build.json",
        {
            "schema": 1,
            "channel": channel,
            "client_version": client["version"],
            "runtime_kind": "compiled" if capacity else "bundled-baseline",
            "capacity": capacity,
            "camera_support": camera,
            "executable_sha256": sha256(output / "runtime/electron"),
            "launch_verified": False,
            "publishable": False,
            "portable": portable,
        },
    )
    if portable:
        (output / "README.txt").write_text(
            f"Discord {channel} with patched Electron\n\n"
            "Extract the whole folder, then run ./launch.sh. You can move the folder.\n"
            "Requires x86-64-v3, Linux desktop libraries, Python 3, PipeWire and PulseAudio tools.\n"
            f"Settings: ~/.config/discord-{channel}-patched-electron.conf\n"
            f"Profile: ~/.config/{name}\n"
            "See https://github.com/discord-patched-electron/discord-patched-electron/blob/main/docs/install.md\n"
        )
    else:
        (output / "README.txt").write_text(
            f"Discord {channel} local test build ({'compiled runtime' if capacity else 'BUNDLED BASELINE ONLY'})\n\n"
            f"Run: {entry}\n\nUses a separate {name}-local profile. Keep this folder at its current path.\n"
            "DPE_APP_AUDIO=0 disables the app-audio adapter; DPE_CAMERA_FAST_PROBE=0 disables the webcam patch.\n"
            "Native Wayland follows your session automatically. Pass --ozone-platform=x11 to test X11.\n"
        )
    print(entry)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--discord", choices=["stable", "ptb", "canary"], default="canary")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--artifact", type=Path, help="Verified fetch_reference.py output directory"
    )
    source.add_argument(
        "--bundled-baseline",
        action="store_true",
        help="Packaging test only; not the custom runtime",
    )
    parser.add_argument(
        "--cache", type=Path, required=True, help="Channel-specific locked client archive cache"
    )
    parser.add_argument("--clients", type=Path, default=ROOT / "config/clients.lock.json")
    parser.add_argument("--portable", action="store_true", help="Build a relocatable release tree")
    args = parser.parse_args()
    build(
        args.output,
        args.discord,
        args.artifact,
        args.cache.resolve(),
        args.clients,
        portable=args.portable,
    )


if __name__ == "__main__":
    main()
