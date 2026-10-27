"""Build client-free prerelease recipes in Arch with a dummy runtime.

This verifies packaging only. Nothing produced by this test is publishable, and
no client or runtime executable is launched. Run as a non-root makepkg user.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

from build_camera_support import build as build_camera
from channels import enabled_discord_channels
from check_release_packages import check_bootstrap_archive
from client_inputs import load_clients
from common import ROOT, digest, download, enabled_electron_channels, read_json, sha256, write_json
from desktop_identity import desktop_id
from generate_package import generate
from updater_python import sources as python_sources


def missing_libraries(relative: str, checksum: str, listing: str) -> list[str]:
    missing = sorted(set(re.findall(r"^\s*(\S+)\s+=> not found\s*$", listing, re.MULTILINE)))
    # Nixpkgs #517309 documents the shipped, effectively unused dispatch module.
    # Keep its unchanged bytes, but never generalize this exception to new modules.
    exception = (
        relative == "discord_dispatch/discord_dispatch.node"
        and checksum == "e5bb33a5c07fdc46f84db2e8fb9c37a3ba9154adeafc614bdc8673b160e180bd"
        and set(missing) <= {"libssl.so.1.1", "libcrypto.so.1.1"}
    )
    if missing and not exception:
        raise ValueError(f"Unresolved native module libraries in {relative}: {missing}")
    return missing


def inspect_package(archive: Path, inputs: dict) -> dict:
    name = inputs["package"]
    root = "usr/lib/" + name
    with tarfile.open(archive) as tar:
        members = {entry.name.rstrip("/"): entry for entry in tar.getmembers()}
        policy = json.load(tar.extractfile(root + "/bootstrap.json"))
        if (
            policy["channel"] != inputs["client"]["channel"]
            or policy["electron_channel"] != inputs["electron_channel"]
        ):
            raise ValueError("Bootstrap channel differs from the recipe")
        check_bootstrap_archive(tar, root)
        if members[root + "/bootstrap.json"].mode & 0o444 != 0o444:
            raise ValueError("Root-owned bootstrap policy must be readable by ordinary users")
        allowed_prefixes = (root + "/", f"usr/share/licenses/{name}/")
        allowed_files = {
            f"usr/bin/{name}",
            f"usr/bin/{desktop_id(inputs['client']['channel'])}",
            f"usr/share/applications/{desktop_id(inputs['client']['channel'])}.desktop",
            f"usr/share/icons/hicolor/256x256/apps/{desktop_id(inputs['client']['channel'])}.png",
        }
        for entry in members.values():
            if (
                not entry.isdir()
                and not entry.name.startswith(".")
                and entry.name not in allowed_files
                and not entry.name.startswith(allowed_prefixes)
            ):
                raise ValueError(f"Package writes outside its private layout: {entry.name}")
    payload = archive.parent / "src/application.zip"
    if sha256(payload) != policy["application"]["archive_sha256"]:
        raise ValueError("Application download pins differ from the prepared payload")
    with zipfile.ZipFile(payload) as application:
        runtime = json.loads(application.read("runtime-update.json"))
        if runtime["archive_sha256"] != inputs["runtime"]["sha256"]:
            raise ValueError("Application runtime pins differ from the recipe")
        camera_hash = hashlib.sha256(application.read("libdpe-camera-probe.so")).hexdigest()
        if camera_hash != inputs["camera_support"]["sha256"]:
            raise ValueError("Application camera helper differs from input")
    return {
        "package": name,
        "client_version": inputs["client"]["version"],
        "electron_channel": inputs["electron_channel"],
        "package_sha256": sha256(archive),
        "package_bytes": archive.stat().st_size,
        "bundled_discord_client": False,
        "bundled_electron": False,
        "camera_support_sha256": camera_hash,
        "runtime_is_test_fixture": True,
        "runtime_validated": False,
        "publishable": False,
    }


def package_one(
    directory: Path, downloads: Path, discord: str, client: dict, electron: str, target: dict
) -> dict:
    fixture = downloads / "runtime.zip"
    runtime = {
        "version": target["version"],
        "runtime_key": "a" * 64,
        "sha256": sha256(fixture),
        "test_fixture": True,
        "url": "https://github.com/discord-patched-electron/discord-patched-electron/releases/download/test-fixture/runtime.zip",
    }
    inputs = generate(
        runtime, client, electron, 1, directory, camera=read_json(downloads / "camera-support.json")
    )
    for filename in (
        "openasar.tar.gz",
        "openasar-cdn.tar.gz",
        "libdpe-camera-probe.so",
    ):
        os.link(downloads / filename, directory / filename)
    for archive in downloads.glob("*.whl"):
        os.link(archive, directory / archive.name)
    with (directory / "makepkg.log").open("w") as log:
        subprocess.run(
            ["makepkg", "--nocheck", "--cleanbuild"],
            cwd=directory,
            env={**os.environ, "PKGEXT": ".pkg.tar"},
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
        )
    with (directory / ".SRCINFO").open("w") as metadata:
        subprocess.run(["makepkg", "--printsrcinfo"], cwd=directory, stdout=metadata, check=True)
    subprocess.run(["desktop-file-validate", str(directory / "discord.desktop")], check=True)
    package = next(directory.glob("*.pkg.tar"))
    report = inspect_package(package, inputs)
    libraries = subprocess.check_output(
        ["ldd", str(directory / "libdpe-camera-probe.so")], text=True
    )
    missing_libraries(
        "libdpe-camera-probe.so", sha256(directory / "libdpe-camera-probe.so"), libraries
    )
    report["camera_library_dependencies_checked"] = True
    return report


def smoke(
    output: Path,
    clients_file: Path = ROOT / "config/clients.lock.json",
    channels: list[str] | None = None,
) -> dict:
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use an empty output directory for the packaging smoke test")
    output.mkdir(parents=True, exist_ok=True)
    downloads = output / "downloads"
    downloads.mkdir()
    build_camera(downloads)
    clients = load_clients(clients_file, channels)
    targets = read_json(ROOT / "config/sources.lock.json")["targets"]
    bootstrap = read_json(ROOT / "config/openasar.lock.json")
    download(bootstrap["url"], downloads / "openasar.tar.gz", bootstrap["sha256"])
    editor = read_json(ROOT / "integration/updater/openasar/source.json")["archive"]
    download(editor["url"], downloads / "openasar-cdn.tar.gz", editor["sha256"])
    for source in python_sources().values():
        download(source["url"], downloads / source["url"].rsplit("/", 1)[-1], source["sha256"])
    fixture = downloads / "runtime.zip"
    with zipfile.ZipFile(fixture, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in (
            "electron",
            "chrome-sandbox",
            "resources.pak",
            "icudtl.dat",
            "LICENSE",
            "LICENSES.chromium.html",
        ):
            entry = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            entry.external_attr = (
                0o100755 if name in ("electron", "chrome-sandbox") else 0o100644
            ) << 16
            archive.writestr(entry, "PACKAGING TEST FIXTURE. NOT AN ELECTRON RUNTIME.\n")
    (output / "arch-packages.txt").write_text(subprocess.check_output(["pacman", "-Q"], text=True))
    results = []
    summary = {
        "schema": 1,
        "selected_channels": list(clients),
        "combinations": results,
        "all_selected_passed": False,
        "runtime_is_test_fixture": True,
        "runtime_validated": False,
        "publishable": False,
    }
    for discord, client in clients.items():
        for electron in enabled_electron_channels():
            target = targets[electron]
            directory = output / (discord + "-" + electron)
            directory.mkdir()
            try:
                report = package_one(directory, downloads, discord, client, electron, target)
                report["passed"] = True
            except Exception as error:
                report = {
                    "passed": False,
                    "phase": "package-validation",
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
            finally:
                # Fixture outputs are never release assets, including failures.
                # Retain downloads, recipes and logs, not staging trees.
                for package in directory.glob("*.pkg.tar"):
                    package.unlink()
                for name in ("src", "pkg"):
                    if (directory / name).exists():
                        shutil.rmtree(directory / name)
            report.update(
                discord_channel=discord,
                electron_channel=electron,
                client_version=client.get("version") if isinstance(client, dict) else None,
                client_digest=digest(client),
                runtime_is_test_fixture=True,
                runtime_validated=False,
                publishable=False,
            )
            write_json(directory / "packaging-report.json", report)
            results.append(report)
            write_json(output / "packaging-report.json", summary)
            print(
                f"{discord}/{electron}: {'packaging passed (dummy runtime)' if report['passed'] else report['error']}",
                flush=True,
            )
    summary["all_selected_passed"] = all(report["passed"] for report in results)
    write_json(output / "packaging-report.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--clients", type=Path, default=ROOT / "config/clients.lock.json")
    parser.add_argument("--discord", action="append", choices=enabled_discord_channels())
    args = parser.parse_args()
    report = smoke(args.output.absolute(), args.clients, args.discord)
    raise SystemExit(0 if report["all_selected_passed"] else 1)


if __name__ == "__main__":
    main()
