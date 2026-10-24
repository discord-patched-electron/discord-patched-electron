"""Build the three prerelease recipes in Arch with real locked clients and a dummy runtime.

This verifies packaging only. Nothing produced by this test is publishable, and
no client or runtime executable is launched. Run as a non-root makepkg user.
"""

from __future__ import annotations

import argparse
import concurrent.futures
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
from client_inputs import load_clients, validate_channel
from common import ROOT, digest, download, enabled_electron_channels, read_json, sha256, write_json
from desktop_identity import desktop_id
from generate_package import generate


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
        info = json.load(tar.extractfile(root + "/resources/build_info.json"))
        if (
            info["releaseChannel"] != inputs["client"]["channel"]
            or info["version"] != inputs["client"]["version"]
            or info["newUpdater"] is not False
            or info["localModulesRoot"] != "/" + root + "/modules"
        ):
            raise ValueError("Packaged client identity, updater or module path is incorrect")
        resources = members[root + "/runtime/resources"]
        if not resources.issym() or resources.linkname != "../resources":
            raise ValueError("Electron and installers must share the packaged resources directory")
        if members[root + "/runtime/chrome-sandbox"].mode != 0o4755:
            raise ValueError("Missing sandbox executable permissions")
        if members[root + "/resources/build_info.json"].mode & 0o444 != 0o444:
            raise ValueError("Root-owned build info must be readable by ordinary users")
        if root + "/modules/discord_krisp/KMS/logs" not in members:
            raise ValueError("Missing Krisp initialization directory")
        for module in inputs["client"]["sources"]:
            if module != "core" and root + "/modules/" + module not in members:
                raise ValueError(f"Missing module: {module}")
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
        patch = json.load(tar.extractfile(root + "/krisp-patch.json"))
        actual = hashlib.file_digest(
            tar.extractfile(root + "/modules/discord_krisp/discord_krisp.node"), "sha256"
        ).hexdigest()
        if actual != patch["output_sha256"]:
            raise ValueError("Packaging modified or stripped the patched Krisp binary")
        audio = json.load(tar.extractfile(root + "/app-audio-patch.json"))
        adapter_hash = hashlib.file_digest(
            tar.extractfile(root + "/modules/discord_voice/dpe-app-audio.cjs"), "sha256"
        ).hexdigest()
        wrapper_hash = hashlib.file_digest(
            tar.extractfile(root + "/modules/discord_voice/index.js"), "sha256"
        ).hexdigest()
        if (
            adapter_hash != audio["adapter_sha256"]
            or wrapper_hash != audio["output_sha256"]
            or audio["enabled_by_default"] is not True
            or audio["default_sessions"] != ["wayland"]
            or audio["disable_environment"] != "DPE_APP_AUDIO=0"
        ):
            raise ValueError("Packaged audio adapter differs from its preparation report")
        for helper, expected in audio["helper_sha256"].items():
            actual = hashlib.file_digest(
                tar.extractfile(root + "/modules/discord_voice/" + helper), "sha256"
            ).hexdigest()
            if actual != expected:
                raise ValueError(
                    "Packaged application audio helper differs from its preparation report"
                )
        storage = json.load(tar.extractfile(root + "/desktop-patch.json"))
        actual = hashlib.file_digest(
            tar.extractfile(root + "/modules/discord_desktop_core/core.asar"), "sha256"
        ).hexdigest()
        if actual != storage["after_sha256"] or storage["plaintext_fallback"]:
            raise ValueError("Packaged desktop storage adapter differs from preparation")
        for filename in (
            "package-inputs.json",
            "runtime/LICENSE",
            "runtime/LICENSES.chromium.html",
        ):
            if root + "/" + filename not in members:
                raise ValueError(f"Missing runtime provenance/license: {filename}")
        camera_hash = hashlib.file_digest(
            tar.extractfile(root + "/libdpe-camera-probe.so"), "sha256"
        ).hexdigest()
        scope = json.load(tar.extractfile(root + "/camera-probe.json"))
        voice_hash = hashlib.file_digest(
            tar.extractfile(root + "/modules/discord_voice/discord_voice.node"), "sha256"
        ).hexdigest()
        config = tar.extractfile(root + "/camera-probe.conf").read().decode()
        if (
            camera_hash != inputs["camera_support"]["sha256"]
            or scope["voice_sha256"] != voice_hash
            or config != f"{scope['start']:#x} {scope['end']:#x}\n"
        ):
            raise ValueError("Packaged camera helper or native scope differs from preparation")
    return {
        "package": name,
        "client_version": inputs["client"]["version"],
        "electron_channel": inputs["electron_channel"],
        "package_sha256": sha256(archive),
        "package_bytes": archive.stat().st_size,
        "krisp_patch": patch,
        "app_audio_patch": audio,
        "camera_support_sha256": camera_hash,
        "camera_scope": scope,
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
    for filename in ("runtime.zip", "LICENSE.html", "OSS-LICENSES.html", "libdpe-camera-probe.so"):
        os.link(downloads / filename, directory / filename)
    for name in client["sources"]:
        os.link(downloads / (discord + "-" + name + ".tar.br"), directory / (name + ".tar.br"))
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
    # The old system-Electron dependency brought libraries transitively.
    # Our private runtime must declare what Discord's own addons need.
    checked = []
    exceptions = {}
    for module in sorted((directory / "src/stage/modules").rglob("*.node")):
        libraries = subprocess.run(
            ["ldd", str(module)], capture_output=True, text=True, check=True
        ).stdout
        relative = str(module.relative_to(directory / "src/stage/modules"))
        missing = missing_libraries(relative, sha256(module), libraries)
        if missing:
            exceptions[relative] = {
                "sha256": sha256(module),
                "missing": missing,
                "source": "https://github.com/NixOS/nixpkgs/pull/517309",
            }
        checked.append(relative)
    report["native_library_dependencies_checked"] = checked
    report["native_library_exceptions"] = exceptions
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
    errors = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {}
        for channel, client in clients.items():
            try:
                validate_channel(channel, client)
            except Exception as error:
                errors[channel] = {"error_type": type(error).__name__, "error": str(error)}
                continue
            for name, source in client["sources"].items():
                future = pool.submit(
                    download,
                    source["url"],
                    downloads / (channel + "-" + name + ".tar.br"),
                    source["sha256"],
                )
                jobs[future] = channel
        for future in concurrent.futures.as_completed(jobs):
            try:
                future.result()
            except Exception as error:
                errors[jobs[future]] = {"error_type": type(error).__name__, "error": str(error)}
    for filename, url in (
        ("LICENSE.html", "https://discord.com/terms"),
        ("OSS-LICENSES.html", "https://discord.com/licenses"),
    ):
        download(url, downloads / filename)
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
                if discord in errors:
                    report = {"passed": False, "phase": "client-download", **errors[discord]}
                else:
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
