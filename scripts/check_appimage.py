"""Exercise the real AppImage builder and runtime without FUSE or Electron."""

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

from appimage import build
from common import ROOT, sha256


def check(tools: Path, container: bool = False):
    with tempfile.TemporaryDirectory(prefix="appimage smoke ") as temporary:
        root = Path(temporary)
        app = root / "bootstrap"
        app.mkdir()
        (app / "probe.py").write_bytes(
            (ROOT / "tests/fixtures/appimage-dependencies.py").read_bytes()
        )
        (app / "launch.sh").write_text(
            '#!/bin/sh\nroot=$(dirname -- "$(readlink -f -- "$0")")\n'
            'exec python3 "$root/probe.py" "$@"\n'
        )
        (app / "launch.sh").chmod(0o755)
        # Public signature fixture from release v20261101094752.0.0-canary.24fa86df36eefd56.
        # Deliberately frozen: tests must verify without fetching a moving release.
        for source, name in (
            (ROOT / "packaging/release-signing-key.asc", "release-signing-key.asc"),
            (ROOT / "tests/fixtures/appimage-SHA256SUMS", "SHA256SUMS"),
            (ROOT / "tests/fixtures/appimage-SHA256SUMS.sig", "SHA256SUMS.sig"),
        ):
            (app / name).write_bytes(source.read_bytes())
        (app / "discord-ptb.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Discord PTB\n"
            "Exec=discord-ptb %U\nIcon=discord-ptb\nCategories=Network;\n"
        )
        (app / "discord.png").write_bytes((ROOT / "packaging/discord-canary.png").read_bytes())
        before = {p.name: sha256(p) for p in app.iterdir()}
        image = root / "DPE PTB.AppImage"
        build(app, image, 1793491200, "ptb", tools)
        if before != {p.name: sha256(p) for p in app.iterdir()}:
            raise ValueError("AppImage packaging modified its source payload")
        args = ["argument with spaces", "discord://test?one=two&three=four", "--flag"]
        command = [str(image), *args]
        if container:
            subprocess.run(
                [
                    "docker",
                    "build",
                    "-f",
                    str(ROOT / "ci/appimage/Test.Dockerfile"),
                    "-t",
                    "dpe-appimage-test",
                    str(ROOT / "ci/appimage"),
                ],
                check=True,
            )
            command = [
                "docker",
                "run",
                "--rm",
                "--network=none",
                "--user",
                "65534:65534",
                "-e",
                "APPIMAGE_EXTRACT_AND_RUN=1",
                "-v",
                f"{root}:/fixture:ro",
                "dpe-appimage-test",
                "/fixture/" + image.name,
                *args,
            ]
            root.chmod(0o755)
        environment = {**os.environ, "APPIMAGE_EXTRACT_AND_RUN": "1"}
        for name in (
            "LD_LIBRARY_PATH",
            "SPA_PLUGIN_DIR",
            "PIPEWIRE_MODULE_DIR",
            "PIPEWIRE_CONFIG_DIR",
            "XDG_DATA_DIRS",
        ):
            if name in environment:
                environment["DPE_TEST_" + name] = environment[name]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            env=environment,
        )
        if result.returncode != 23 or json.loads(result.stdout) != args:
            raise ValueError(f"AppImage launch failed: {result}")
        print("AppImage build, extraction, launch, argument forwarding and exit status passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", type=Path, default=ROOT / "out/appimage-tools")
    parser.add_argument("--container", action="store_true")
    args = parser.parse_args()
    check(args.tools, args.container)
