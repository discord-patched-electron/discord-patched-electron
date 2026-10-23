"""Build all nine recipes in Arch with real locked clients and a dummy runtime.

This verifies packaging only. Nothing produced by this test is publishable, and
no client or runtime executable is launched. Run as a non-root makepkg user.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import zipfile

from common import ROOT, download, read_json, sha256, write_json
from generate_package import generate


def inspect_package(archive: Path, inputs: dict) -> dict:
    name = inputs["package"]
    root = "usr/lib/" + name
    with tarfile.open(archive) as tar:
        members = {entry.name.rstrip("/"): entry for entry in tar.getmembers()}
        info = json.load(tar.extractfile(root + "/resources/build_info.json"))
        if (info["releaseChannel"] != inputs["client"]["channel"]
                or info["version"] != inputs["client"]["version"]
                or info["newUpdater"] is not False
                or info["localModulesRoot"] != "/" + root + "/modules"):
            raise ValueError("Packaged client identity, updater or module path is incorrect")
        if members[root + "/runtime/chrome-sandbox"].mode != 0o4755:
            raise ValueError("Missing sandbox executable permissions")
        if root + "/modules/discord_krisp/KMS/logs" not in members:
            raise ValueError("Missing Krisp initialization directory")
        for module in inputs["client"]["sources"]:
            if module != "core" and root + "/modules/" + module not in members:
                raise ValueError(f"Missing module: {module}")
        allowed_prefixes = (root + "/", f"usr/share/licenses/{name}/")
        allowed_files = {f"usr/bin/{name}", f"usr/share/applications/{name}.desktop",
                         f"usr/share/icons/hicolor/256x256/apps/{name}.png"}
        for entry in members.values():
            if (not entry.isdir() and not entry.name.startswith(".")
                    and entry.name not in allowed_files and not entry.name.startswith(allowed_prefixes)):
                raise ValueError(f"Package writes outside its private layout: {entry.name}")
        patch = json.load(tar.extractfile(root + "/krisp-patch.json"))
        actual = hashlib.file_digest(tar.extractfile(root + "/modules/discord_krisp/discord_krisp.node"), "sha256").hexdigest()
        if actual != patch["output_sha256"]:
            raise ValueError("Packaging modified or stripped the patched Krisp binary")
        for filename in ("package-inputs.json", "runtime/LICENSE", "runtime/LICENSES.chromium.html"):
            if root + "/" + filename not in members:
                raise ValueError(f"Missing runtime provenance/license: {filename}")
    return {"package": name, "client_version": inputs["client"]["version"],
            "electron_channel": inputs["electron_channel"], "package_sha256": sha256(archive),
            "package_bytes": archive.stat().st_size, "krisp_patch": patch,
            "runtime_is_test_fixture": True, "runtime_validated": False, "publishable": False}


def smoke(output: Path):
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use an empty output directory for the packaging smoke test")
    output.mkdir(parents=True, exist_ok=True)
    downloads = output / "downloads"
    downloads.mkdir()
    clients = read_json(ROOT / "config/clients.lock.json")["clients"]
    targets = read_json(ROOT / "config/sources.lock.json")["targets"]
    jobs = [(source["url"], downloads / (channel + "-" + name + ".tar.br"), source["sha256"])
            for channel, client in clients.items() for name, source in client["sources"].items()]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda args: download(*args), jobs))
    for filename, url in (("LICENSE.html", "https://discord.com/terms"), ("OSS-LICENSES.html", "https://discord.com/licenses")):
        download(url, downloads / filename)
    fixture = downloads / "runtime.zip"
    with zipfile.ZipFile(fixture, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in ("electron", "chrome-sandbox", "resources.pak", "icudtl.dat", "LICENSE", "LICENSES.chromium.html"):
            entry = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            entry.external_attr = (0o100755 if name in ("electron", "chrome-sandbox") else 0o100644) << 16
            archive.writestr(entry, "PACKAGING TEST FIXTURE. NOT AN ELECTRON RUNTIME.\n")
    (output / "arch-packages.txt").write_text(subprocess.check_output(["pacman", "-Q"], text=True))
    results = []
    for discord, client in clients.items():
        for electron, target in targets.items():
            directory = output / (discord + "-" + electron)
            runtime = {"version": target["version"], "runtime_key": "a" * 64,
                "sha256": sha256(fixture), "test_fixture": True,
                "url": "https://github.com/discord-patched-electron/discord-patched-electron/releases/download/test-fixture/runtime.zip"}
            inputs = generate(runtime, client, electron, 1, directory)
            for filename in ("runtime.zip", "LICENSE.html", "OSS-LICENSES.html"):
                os.link(downloads / filename, directory / filename)
            for name in client["sources"]:
                os.link(downloads / (discord + "-" + name + ".tar.br"), directory / (name + ".tar.br"))
            with (directory / "makepkg.log").open("w") as log:
                subprocess.run(["makepkg", "--nocheck", "--cleanbuild"], cwd=directory,
                    env={**os.environ, "PKGEXT": ".pkg.tar"}, stdout=log, stderr=subprocess.STDOUT, check=True)
            with (directory / ".SRCINFO").open("w") as metadata:
                subprocess.run(["makepkg", "--printsrcinfo"], cwd=directory, stdout=metadata, check=True)
            subprocess.run(["desktop-file-validate", str(directory / "discord.desktop")], check=True)
            package = next(directory.glob("*.pkg.tar"))
            report = inspect_package(package, inputs)
            # The old system-Electron dependency brought libraries transitively.
            # Our private runtime must declare what Discord's own addons need.
            checked = []
            for module in sorted((directory / "src/stage/modules").rglob("*.node")):
                libraries = subprocess.run(["ldd", str(module)], capture_output=True, text=True, check=True).stdout
                if "not found" in libraries:
                    raise ValueError(f"Unresolved native module libraries in {module}:\n{libraries}")
                checked.append(str(module.relative_to(directory / "src/stage/modules")))
            report["native_library_dependencies_checked"] = checked
            write_json(directory / "packaging-report.json", report)
            results.append(report)
            print(inputs["package"] + ": packaging passed (dummy runtime, not publishable)", flush=True)
            # These are disposable fixture artifacts, never release assets. Keep
            # sources, generated recipes and reports; avoid nine full staging trees.
            package.unlink()
            shutil.rmtree(directory / "src")
            shutil.rmtree(directory / "pkg")
    write_json(output / "packaging-report.json", {"schema": 1, "combinations": results,
        "runtime_is_test_fixture": True, "runtime_validated": False, "publishable": False})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    smoke(args.output.absolute())


if __name__ == "__main__":
    main()
