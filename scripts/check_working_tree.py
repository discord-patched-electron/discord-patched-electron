"""Build and qualify the working tree using a verified runtime and fresh profiles.

Never installs a system package, uses an account, or publishes a release.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from common import ROOT, read_json, sha256, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, default=ROOT / "out/cache-migration-check")
    parser.add_argument("--clients", type=Path, default=ROOT / "config/clients.lock.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--nfpm", default=str(ROOT / "out/tools/nfpm"))
    args = parser.parse_args()
    artifact = args.artifact.resolve()
    output = (
        args.output or ROOT / "out" / datetime.now(UTC).strftime("working-tree-%Y%m%d-%H%M%S")
    ).resolve()
    if output.exists():
        parser.error("Choose a fresh output directory")
    archive = artifact / "binary/runtime-dist.zip"
    provenance = read_json(artifact / "artifact-provenance.json")
    policy = read_json(ROOT / "config/update-policy.json")["last_good_runtime"]
    if (
        sha256(archive) != policy["archive_sha256"]
        or provenance["capacity"]["archive_sha256"] != policy["archive_sha256"]
    ):
        parser.error("Artifact must match the currently approved runtime archive")
    if not Path(args.nfpm).is_file() and not shutil.which(args.nfpm):
        parser.error("Pass --nfpm with the verified nFPM executable")
    output.mkdir(parents=True)
    results = []
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "XDG_SESSION_TYPE": "x11"}
    environment.pop("WAYLAND_DISPLAY", None)
    environment.pop("ELECTRON_RUN_AS_NODE", None)

    def run(name, *command, desktop=False):
        argv = [str(part) for part in command]
        if desktop:
            argv = ["xvfb-run", "-a", "-s", "-screen 0 1440x1000x24", *argv]
        print(f"{name}: {output / (name + '.log')}", flush=True)
        with (output / (name + ".log")).open("w") as log:
            result = subprocess.run(
                argv, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT
            )
        results.append({"check": name, "passed": result.returncode == 0})
        write_json(
            output / "report.json",
            {"passed": all(item["passed"] for item in results), "checks": results},
        )
        if result.returncode:
            raise SystemExit(f"{name} failed; see {output / (name + '.log')}")

    def python(name, script, *arguments, desktop=False):
        run(name, sys.executable, ROOT / "scripts" / script, *arguments, desktop=desktop)

    run("lint", "bun", "run", "lint")
    run("unit-tests", "bun", "run", "test")
    app = output / "app"
    python(
        "build",
        "build_local_app.py",
        "--portable",
        "--artifact",
        artifact,
        "--clients",
        args.clients,
        "--cache",
        ROOT / "out/downloads/canary",
        "--output",
        app,
    )
    python(
        "settings",
        "check_launch_settings.py",
        artifact / "runtime/electron",
        "--output",
        output / "settings",
        desktop=True,
    )
    python(
        "first-launch",
        "check_desktop_launch.py",
        app,
        "--output",
        output / "first-launch",
        "--relaunch",
        "--expect-setup",
        desktop=True,
    )
    # Use an app.asar harness, since retained Electron distributions may already
    # contain resources/app and ignore a JavaScript path on the command line.
    picker_runtime = output / "picker-runtime"
    run("picker-runtime", "cp", "-a", "--reflink=auto", artifact / "runtime", picker_runtime)
    resources = picker_runtime / "resources"
    if (resources / "app").exists():
        shutil.rmtree(resources / "app")
    fixture = output / "picker-fixture"
    fixture.mkdir()
    (fixture / "package.json").write_text('{"main":"main.cjs"}')
    for name, test in (
        ("picker", "picker-smoke.cjs"),
        ("discord-picker", "discord-picker-smoke.cjs"),
    ):
        (fixture / "main.cjs").write_text(
            "require(" + json.dumps(str(ROOT / "tests/audio" / test)) + ");"
        )
        run(
            name + "-fixture",
            ROOT / "node_modules/.bin/asar",
            "pack",
            fixture,
            resources / "app.asar",
        )
        environment["DPE_PICKER_OUTPUT"] = str(output / name)
        environment.pop("ELECTRON_RUN_AS_NODE", None)
        run(name, picker_runtime / "electron", "--disable-setuid-sandbox", desktop=True)
    environment.pop("DPE_PICKER_OUTPUT", None)
    now = datetime.now(UTC)
    packages = output / "packages"
    python(
        "packages",
        "package_release.py",
        app,
        "--output",
        packages,
        "--version",
        now.strftime("%Y%m%d.%H%M%S"),
        "--epoch",
        int(now.timestamp()),
        "--nfpm",
        args.nfpm,
    )
    python("package-check", "check_release_packages.py", packages)
    # Both installed-bootstrap paths start without a bundled Electron or app.
    # The first really downloads the pinned runtime; only the unpublished app is seeded.
    python(
        "no-electron-first-launch",
        "check_desktop_launch.py",
        packages / "payload",
        "--application-archive",
        packages / "application.zip",
        "--output",
        output / "no-electron",
        "--relaunch",
        "--expect-setup",
        desktop=True,
    )
    python(
        "cached-runtime-first-launch",
        "check_desktop_launch.py",
        packages / "payload",
        "--application-archive",
        packages / "application.zip",
        "--runtime-archive",
        archive,
        "--output",
        output / "cached-runtime",
        "--relaunch",
        "--setup-configure",
        desktop=True,
    )
    python(
        "application-update",
        "check_application_update.py",
        packages,
        "--runtime-archive",
        archive,
        "--output",
        output / "application-update",
        desktop=True,
    )
    python(
        "mods",
        "check_client_mods.py",
        app,
        "--vencord",
        ROOT / "out/tools/VencordInstallerCli-linux",
        "--equicord",
        ROOT / "out/tools/EquilotlCli-linux",
        "--output",
        output / "mods",
        desktop=True,
    )
    print(f"All checks passed. Reports: {output}")
    print(
        f"Manual fresh-profile launch: DPE_PROFILE_DIR='{output / 'manual-profile'}' '{app / 'launch.sh'}'"
    )


if __name__ == "__main__":
    main()
