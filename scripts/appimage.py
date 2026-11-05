"""Build an AppImage wrapper around the unchanged, verified bootstrap."""

import argparse
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from common import ROOT, download, read_json, sha256, write_json
from desktop_identity import desktop_id

# Shared by dependency generation, packaging and extracted-release validation.
# No desktop stack may enter the image, even via a transitive dependency.
HOST_LIBRARIES = re.compile(
    r"lib(?:pipewire|spa-|pulse|asound|gtk|gdk|glib-|gobject-|gio-|gmodule-|"
    r"nss|nspr|smime|ssl3|softokn|freebl|notify|secret|cups|fontconfig|freetype|"
    r"harfbuzz|fribidi|pango|cairo|gbm|drm|GL|EGL|vulkan|wayland|X11|xcb|"
    r"c\.so|m\.so|pthread|dl\.so|stdc\+\+|gcc_s)|ld-linux"
)


def validate_dependencies(directory: Path):
    metadata = read_json(directory / "usr/share/dpe-dependencies/build.json")
    if (
        metadata.get("schema") != 2
        or metadata.get("recipe_sha256") != sha256(ROOT / "scripts/stage_appimage_dependencies.sh")
        or metadata.get("container_sha256") != sha256(ROOT / "ci/appimage/Dockerfile")
        or metadata.get("tools") != read_json(ROOT / "config/appimage-tools.json")
    ):
        raise ValueError("Stale AppImage dependencies; rebuild in a fresh tools directory")
    for path in directory.rglob("*"):
        relative = path.relative_to(directory)
        if (
            (".so" in path.name and HOST_LIBRARIES.match(path.name))
            or path.name in ("pipewire-0.3", "spa-0.2", "pipewire", "desktop")
            or (
                relative.parent == Path("usr/bin")
                and path.name not in ("python3", "gpg", "gpgv", "brotli")
            )
        ):
            raise ValueError(f"Host-owned dependency in AppImage: {relative}")


def install_tools(directory: Path):
    directory = directory.resolve()
    for name, source in read_json(ROOT / "config/appimage-tools.json").items():
        path = download(source["url"], directory / name, source["sha256"])
        if not name.endswith(".tar.gz"):
            path.chmod(0o755)


def install_dependencies(tools: Path):
    tools = tools.resolve()
    destination = tools / "dependencies"
    if destination.exists():
        raise ValueError("Choose a fresh dependency output directory")
    subprocess.run(
        ["docker", "build", "--tag", "dpe-appimage-dependencies", str(ROOT / "ci/appimage")],
        check=True,
    )
    with tempfile.TemporaryDirectory(prefix="dependencies-", dir=tools) as temporary:
        staged = Path(temporary) / "payload"
        staged.mkdir()
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--network=none",
                "-v",
                f"{tools}:/tools:ro",
                "-v",
                f"{staged}:/output",
                "-v",
                f"{ROOT / 'scripts/stage_appimage_dependencies.sh'}:/stage.sh:ro",
                "dpe-appimage-dependencies",
                "bash",
                "/stage.sh",
                "/tools",
                "/output",
            ],
            check=True,
        )
        write_json(
            staged / "usr/share/dpe-dependencies/build.json",
            {
                "schema": 2,
                "tools": read_json(ROOT / "config/appimage-tools.json"),
                "recipe_sha256": sha256(ROOT / "scripts/stage_appimage_dependencies.sh"),
                "container_sha256": sha256(ROOT / "ci/appimage/Dockerfile"),
                "bundled_electron": False,
                "bundled_discord_client": False,
            },
        )
        validate_dependencies(staged)
        staged.rename(destination)


def stage(app: Path, destination: Path, channel: str):
    identity = desktop_id(channel)
    # Nest the bootstrap so its desktop file and checksum list remain byte-identical
    # to the native packages. Only the outer AppDir has AppImage-specific metadata.
    shutil.copytree(app, destination / "usr/lib/dpe")
    shutil.copy2(ROOT / "packaging/linux/appimage-apprun.sh", destination / "AppRun")
    (destination / "AppRun").chmod(0o755)
    content = (app / f"{identity}.desktop").read_text()
    content = re.sub(r"^Exec=.*", "Exec=AppRun %U", content, flags=re.MULTILINE)
    content = re.sub(r"^Icon=.*", f"Icon={identity}", content, flags=re.MULTILINE)
    content = re.sub(r"^(TryExec|Path)=.*\n?", "", content, flags=re.MULTILINE)
    (destination / f"{identity}.desktop").write_text(content)
    shutil.copy2(app / "discord.png", destination / f"{identity}.png")
    (destination / ".DirIcon").symlink_to(f"{identity}.png")


def build(app: Path, destination: Path, epoch: int, channel: str, tools: Path):
    tool, runtime = tools.resolve() / "appimagetool", tools.resolve() / "runtime-x86_64"
    if (
        not tool.is_file()
        or not runtime.is_file()
        or not (tools / "dependencies/usr/bin/python3").is_file()
        or not (tools / "dependencies/usr/share/dpe-dependencies/build.json").is_file()
    ):
        raise ValueError("Install pinned AppImage tools with scripts/appimage.py first")
    validate_dependencies(tools / "dependencies")
    with tempfile.TemporaryDirectory(prefix="dpe-appimage-") as temporary:
        appdir = Path(temporary) / "DPE.AppDir"
        shutil.copytree(tools / "dependencies", appdir, symlinks=True)
        stage(app, appdir, channel)
        subprocess.run(
            [
                str(tool),
                "--no-appstream",
                "--runtime-file",
                str(runtime),
                str(appdir),
                str(destination.resolve()),
            ],
            check=True,
            env={
                **os.environ,
                "ARCH": "x86_64",
                "SOURCE_DATE_EPOCH": str(epoch),
                "APPIMAGE_EXTRACT_AND_RUN": "1",
            },
        )
    if not destination.is_file():
        raise ValueError("appimagetool did not produce an AppImage")
    destination.chmod(0o755)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", type=Path, required=True)
    parser.add_argument("--dependencies", action="store_true")
    args = parser.parse_args()
    install_tools(args.install)
    if args.dependencies:
        install_dependencies(args.install)
