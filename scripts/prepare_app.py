"""Apply the current AUR resource/bootstrap fixes to an extracted Discord core.

Only operates on a staging tree, never a user's installed client or profile.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from common import enabled_electron_channels, ROOT, package_name, read_json, write_json


def replace(text: str, before: str, after: str, *, exact: int | None = 1) -> str:
    count = text.count(before)
    if (exact is not None and count != exact) or (exact is None and count == 0):
        raise ValueError(f"Unsupported bootstrap: expected {exact or 'at least one'} occurrence(s) of {before!r}; got {count}")
    return text.replace(before, after)


def patch_bundle(text: str, package: str, install_root: Path | None = None) -> str:
    resource = json.dumps(str((install_root or Path(f"/usr/lib/{package}")) / "resources"))
    text = replace(text, 'resourcesPath=path_1.default.join(require.main.filename,"..",".."),', f"resourcesPath={resource},")
    text = replace(text, "process.resourcesPath", resource, exact=None)
    # A package manager owns both host and modules. The legacy updater already
    # skips module updates for localModulesRoot, but otherwise blocks the entire
    # app whenever Discord publishes a newer host while CI is still compiling.
    text = replace(text, 'skipHostUpdate=settings?.get("SKIP_HOST_UPDATE")||!hostUpdatable',
                   'skipHostUpdate=locallyInstalledModules||settings?.get("SKIP_HOST_UPDATE")||!hostUpdatable')
    text = replace(text, "Exec=${exePath}", f"Exec=/usr/bin/{package}")
    text = replace(text, "Name=${appName}", f"Name={package}")
    return replace(text, "Icon=${iconPath}", f"Icon={package}")


def prepare(core: Path, discord: str, electron: str, asar: str, *, install_root: Path | None = None) -> None:
    package = package_name(discord, electron)
    install_root = install_root or Path(f"/usr/lib/{package}")
    if not install_root.is_absolute():
        raise ValueError("Install root must be absolute")
    resources = core / "resources"
    archive = resources / "app.asar"
    info_path = resources / "build_info.json"
    info = read_json(info_path)
    if info["releaseChannel"] != discord:
        raise ValueError("Discord releaseChannel differs from selected package")
    with tempfile.TemporaryDirectory(prefix="app-", dir=core) as work:
        extracted = Path(work) / "app"
        subprocess.run([asar, "extract", str(archive), str(extracted)], check=True)
        bundle = extracted / "bundle.js"
        bundle.write_text(patch_bundle(bundle.read_text(), package, install_root))
        splash = extracted / "splashScreenPreload.js"
        splash.write_text(replace(splash.read_text(), "process.resourcesPath", json.dumps(str(install_root / "resources"))))
        metadata = read_json(extracted / "package.json")
        metadata["desktopName"] = f"{package}.desktop"
        write_json(extracted / "package.json", metadata)
        result = Path(work) / "app.asar"
        subprocess.run([asar, "pack", str(extracted), str(result)], check=True)
        result.replace(archive)
    info["newUpdater"] = False
    info["localModulesRoot"] = str(install_root / "modules")
    write_json(info_path, info)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("core", type=Path)
    parser.add_argument("--discord", required=True, choices=["stable", "ptb", "canary"])
    parser.add_argument("--electron", required=True, choices=enabled_electron_channels())
    parser.add_argument("--asar", default=str(ROOT / "node_modules/.bin/asar"))
    args = parser.parse_args()
    prepare(args.core, args.discord, args.electron, args.asar)


if __name__ == "__main__":
    main()
