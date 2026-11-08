#!/usr/bin/env python3
"""Launch one command with bundled DPE game hooks; never attach or register globally."""

import argparse
import json
import os
import re
import shutil
import stat
import sys
from pathlib import Path


def launch_environment(directory, socket_path, environment):
    # A game already launched with vanilla obs-gamecapture needs no second GL
    # interposer/Vulkan layer. Its ordinary hook is handled by our OBS listener.
    loaded = re.split(r"[:\s]+", environment.get("LD_PRELOAD", ""))
    if environment.get("OBS_COMPAT") == "1" or any(
        Path(item).name == "libobs_glcapture.so" for item in loaded if item
    ):
        return dict(environment)
    root = Path(directory).resolve(strict=True)
    if any(c.isspace() or c == ":" for c in str(root)):
        raise ValueError("Game capture bundle path cannot contain loader separators")
    manifest = json.loads((root / "backend.json").read_text())
    if manifest.get("protocol") != 1:
        raise ValueError("Unsupported DPE game capture bundle")
    if (
        not socket_path.startswith("/")
        or len(socket_path.encode()) >= 108
        or any(c in socket_path for c in ":\\\n\r")
    ):
        raise ValueError("Invalid DPE game capture socket")
    gl = root / "libobs_glcapture.so"
    layer = root / "layers"
    for path in (gl, layer / "dpe_vkcapture.json", root / "libVkLayer_obs_vkcapture.so"):
        if not path.is_file():
            raise ValueError("Incomplete DPE game capture bundle")
    result = dict(environment)
    vulkan_only = result.pop("OBS_VKCAPTURE", None) == "1"
    # Translate the selection to our explicit layer, without enabling the stock
    # implicit OBS layer alongside it.
    result["DPE_VKCAPTURE_SOCKET"] = socket_path
    # Proton's pressure-vessel runtime hides arbitrary XDG_RUNTIME_DIR entries.
    # Mount the directory, not the socket inode, so a later picker/broker restart
    # is visible too. Read-only mounts still permit Unix-socket connections.
    shared = [str(root), str(Path(socket_path).parent)]
    existing = result.get("PRESSURE_VESSEL_FILESYSTEMS_RO", "")
    result["PRESSURE_VESSEL_FILESYSTEMS_RO"] = ":".join(([existing] if existing else []) + shared)
    # Additive loader variables preserve the application's existing layers.
    result["VK_ADD_LAYER_PATH"] = str(layer) + (
        ":" + result["VK_ADD_LAYER_PATH"] if result.get("VK_ADD_LAYER_PATH") else ""
    )
    result["VK_INSTANCE_LAYERS"] = "VK_LAYER_DPE_gamecapture_64" + (
        ":" + result["VK_INSTANCE_LAYERS"] if result.get("VK_INSTANCE_LAYERS") else ""
    )
    if not vulkan_only:
        # Steam's overlay interposes loader functions too. It must retain its
        # earlier position: putting our GL hook first can hang before exec/game.
        result["LD_PRELOAD"] = (
            result["LD_PRELOAD"] + ":" if result.get("LD_PRELOAD") else ""
        ) + str(gl)
    return result


def prepare_socket_directory(socket_path):
    directory = Path(socket_path).parent
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError("Game capture requires an owned private runtime directory")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", default=str(Path(__file__).resolve().parent))
    parser.add_argument(
        "--socket",
        default=str(
            Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
            / "dpe-gamecapture/hook.sock"
        ),
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("Provide a game executable after --")
    if command[0] in ("game-command", "%command%"):
        raise ValueError(
            "Use %command% in Steam launch options only. "
            "In a terminal, replace it with your game's executable and arguments."
        )
    if os.environ.get("OBS_COMPAT") == "1":
        env = dict(os.environ)
        if env.get("OBS_VKCAPTURE") != "1":
            upstream = shutil.which("obs-gamecapture")
            if not upstream:
                raise ValueError("OBS_COMPAT=1 requires the installed obs-gamecapture command")
            command = [upstream, *command]
        os.execvpe(command[0], command, env)
        return
    env = launch_environment(args.bundle, args.socket, os.environ)
    if env.get("DPE_VKCAPTURE_SOCKET"):
        prepare_socket_directory(env["DPE_VKCAPTURE_SOCKET"])
    os.execvpe(command[0], command, env)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        print(f"DPE game capture: {error}", file=sys.stderr)
        raise SystemExit(2) from None
