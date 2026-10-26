"""Verify official mods survive a package update with a fresh, unauthenticated profile."""

import argparse
import os
import subprocess
import sys
from pathlib import Path

from check_desktop_launch import check
from common import ROOT, read_json, sha256, write_json

sys.path.insert(0, str(ROOT / "integration/updater"))
from client_mods import detect
from installation import copy_payload


def verify(app: Path, output: Path, installers: dict[str, Path]):
    app, output = app.resolve(), output.resolve()
    if output.exists():
        raise ValueError("Choose a fresh mod report directory")
    output.mkdir(parents=True)
    profile = output / "profile"
    os.environ["VENCORD_USER_DATA_DIR"] = str(profile / "vencord")
    os.environ["EQUICORD_USER_DATA_DIR"] = str(profile / "equicord")
    for name in ("vencord", "equicord"):
        (profile / name).mkdir(parents=True)

    # Reuse the verified runtime. A new bootstrap in a relocated portable tree
    # exercises the same publication path as installing an updated system package.
    upgraded = output / "updated app"
    copy_payload(app, upgraded)
    bootstrap = output / "bootstrap"
    asar = str(ROOT / "node_modules/.bin/asar")
    archive = upgraded / "resources/app.asar"
    subprocess.run([asar, "extract", str(archive), str(bootstrap)], check=True)
    with (bootstrap / "main.cjs").open("a") as script:
        script.write("\n// Client-mod package update qualification.\n")
    subprocess.run([asar, "pack", str(bootstrap), str(archive)], check=True)
    policy = read_json(upgraded / "client-distribution.json")
    policy["loader_sha256"] = sha256(archive)
    write_json(upgraded / "client-distribution.json", policy)
    updated_loader = archive.read_bytes()
    report = {}

    def launch(directory, label, *, mod=False, self_updated=False):
        result = check(directory, output / label, profile, equicord=mod, read_only_app=True)
        if not result["passed"]:
            raise ValueError(f"Client-mod launch failed: {label}")
        if self_updated and result["page"].get("modSelfUpdate") != "dpe-self-update-v2":
            raise ValueError(f"Updated mod renderer was not loaded: {label}")
        return profile / ("app-" + result["client_version"]) / "resources"

    for name, installer in installers.items():
        label = name.lower()
        resources = launch(app, label + "-plain")
        command = [str(installer.resolve())]
        try:
            subprocess.run([*command, "-install", "-location", str(profile)], check=True)
            installed = detect(resources)
            if installed is None or installed.name != name:
                raise ValueError(f"Official {name} installation was not detected")
            launch(app, label + "-installed", mod=True)
            # Exercise the real downloaded mod's updater, using deterministic
            # release responses that change its executable payload on disk.
            self_update = output / (label + "-self-update")
            subprocess.run(
                [
                    "node",
                    str(ROOT / "tests/client-mods/self-update.cjs"),
                    name,
                    installed.target,
                    str(self_update),
                ],
                check=True,
                timeout=30,
            )
            updated_mod = read_json(self_update / "report.json")
            launch(app, label + "-self-updated", mod=True, self_updated=True)
            resources = launch(upgraded, label + "-updated", mod=True, self_updated=True)
            if (resources / "app.asar").read_bytes() != installed.loader:
                raise ValueError(f"{name} loader changed during update")
            if (resources / "_app.asar").read_bytes() != updated_loader:
                raise ValueError(f"{name} uninstall backup was not refreshed")
            launch(upgraded, label + "-relaunched", mod=True, self_updated=True)
            for filename, expected in updated_mod["after"].items():
                if sha256(Path(filename)) != expected:
                    raise ValueError(f"{name} self-update was overwritten: {filename}")
            report[name] = {
                "detected": installed.name,
                "loader_sha256": sha256(resources / "app.asar"),
                "updated_bootstrap_sha256": sha256(resources / "_app.asar"),
                "preserved_without_reinstall": True,
                "self_update_preserved": True,
                "mod_files": updated_mod["after"],
            }
        finally:
            if (resources / "_app.asar").exists():
                subprocess.run([*command, "-uninstall", "-location", str(profile)], check=True)
        if (resources / "app.asar").read_bytes() != updated_loader:
            raise ValueError(f"{name} uninstall restored the wrong bootstrap")
        launch(upgraded, label + "-uninstalled")
        report[name]["uninstall_verified"] = True
    write_json(output / "mod-report.json", report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--vencord", type=Path, required=True)
    parser.add_argument("--equicord", type=Path, required=True)
    args = parser.parse_args()
    verify(args.app, args.output, {"Vencord": args.vencord, "Equicord": args.equicord})
