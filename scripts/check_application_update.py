"""Qualify a signed application upgrade with fresh, unauthenticated desktop profiles.

Only a disposable fixture key is used. No real release is published or approved.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from application_payload import bundle
from check_desktop_launch import check
from common import ROOT, read_json, sha256, write_json

sys.path.insert(0, str(ROOT / "integration/updater"))
from installation import copy_payload


def verify(packages: Path, output: Path, runtime_archive: Path):
    packages, output = packages.resolve(), output.resolve()
    if output.exists():
        raise ValueError("Choose a fresh application-upgrade report directory")
    output.mkdir(parents=True)
    package = output / "installed bootstrap"
    shutil.copytree(packages / "payload", package)
    profile = output / "profile"
    with tempfile.TemporaryDirectory(prefix="dpe-application-fixture-key-") as temporary:
        signer = ["gpg", "--batch", "--homedir", temporary]
        subprocess.run(
            [
                *signer,
                "--pinentry-mode",
                "loopback",
                "--passphrase",
                "",
                "--quick-generate-key",
                "Disposable application qualification",
                "ed25519",
                "sign",
                "1d",
            ],
            check=True,
            capture_output=True,
        )
        try:
            (package / "release-signing-key.asc").write_bytes(
                subprocess.check_output([*signer, "--armor", "--export"])
            )
            initial = check(
                package,
                output / "initial",
                profile,
                read_only_app=True,
                runtime_archive=runtime_archive,
                application_archive=packages / "application.zip",
            )
            if not initial["passed"]:
                raise ValueError("Initial bootstrap launch failed")
            original = Path(
                subprocess.check_output(
                    [
                        sys.executable,
                        "-I",
                        str(package / "loader.py"),
                        "--profile",
                        str(profile),
                        "--resolve",
                    ],
                    text=True,
                ).strip()
            )
            old_files = {
                str(p.relative_to(original)): sha256(p) for p in original.rglob("*") if p.is_file()
            }
            source = output / "next application"
            copy_payload(original, source)
            (source / "application-index.json").unlink()
            for filename, comment in (
                ("integration/updater/client.py", "\n# Application upgrade qualification.\n"),
                ("integration/updater/patches.cjs", "\n// Application upgrade qualification.\n"),
                ("bootstrap/src/index.js", "\n// Application upgrade qualification.\n"),
            ):
                with (source / filename).open("a") as stream:
                    stream.write(comment)
            unpacked = output / "new loader"
            asar = str(ROOT / "node_modules/.bin/asar")
            subprocess.run(
                [asar, "extract", str(source / "resources/app.asar"), str(unpacked)], check=True
            )
            with (unpacked / "main.cjs").open("a") as stream:
                stream.write("\n// Application upgrade qualification.\n")
            subprocess.run(
                [asar, "pack", str(unpacked), str(source / "resources/app.asar")], check=True
            )
            distribution = read_json(source / "client-distribution.json")
            distribution["loader_sha256"] = sha256(source / "resources/app.asar")
            write_json(source / "client-distribution.json", distribution)
            manifest = read_json(packages / "release.json")
            version = (
                datetime.strptime(manifest["version"], "%Y%m%d.%H%M%S") + timedelta(seconds=1)
            ).strftime("%Y%m%d.%H%M%S")
            policy = read_json(source / "runtime-update.json")
            policy["release_version"] = version
            write_json(source / "runtime-update.json", policy)
            archive = output / "application.zip"
            incoming = bundle(source, archive, version, 1793102400)
            manifest.update(
                application=incoming,
                version=version,
                source_dirty=False,
                qualification_fixture=True,
                assets={"application.zip": incoming["archive_sha256"]},
            )
            proof = output / "fixture-proof"
            proof.mkdir()
            write_json(proof / "release.json", manifest)
            digest = sha256(proof / "release.json")
            (proof / "SHA256SUMS").write_text(
                digest + "  release.json\n" + incoming["archive_sha256"] + "  application.zip\n"
            )
            subprocess.run(
                [*signer, "--detach-sign", str(proof / "SHA256SUMS")],
                check=True,
                capture_output=True,
            )
            tag = "qualification-" + version
            write_json(
                proof / "github.json",
                {
                    "tag_name": tag,
                    "draft": False,
                    "prerelease": False,
                    "assets": [
                        {
                            "name": name,
                            "browser_download_url": f"https://github.com/discord-patched-electron/discord-patched-electron/releases/download/{tag}/{name}",
                        }
                        for name in ("release.json", "SHA256SUMS", "SHA256SUMS.sig")
                    ],
                },
            )
            write_json(
                proof / "approval.json",
                {
                    "schema": 1,
                    "tag": tag,
                    "version": version,
                    "manifest_sha256": digest,
                    "approved_by": "disposable-qualification-fixture",
                },
            )
            sys.path.insert(0, str(original / "integration/updater"))
            sys.dont_write_bytecode = True
            import application
            import runtime

            def fixture_download(url, target, **_kwargs):
                name = (
                    "github.json"
                    if url.endswith("/latest")
                    else "approval.json"
                    if "/config/" in url
                    else url.rsplit("/", 1)[-1]
                )
                shutil.copyfile(archive if name == "application.zip" else proof / name, target)

            with (
                patch.dict(
                    os.environ,
                    {
                        "DPE_INSTALL_ROOT": str(package),
                        "DPE_CLIENT_PINNED": "0",
                        "DPE_CLIENT_OFFLINE": "0",
                    },
                ),
                patch.object(runtime, "download", side_effect=fixture_download),
                patch.object(application, "download", side_effect=fixture_download),
            ):
                selected = application.prepare(original, profile)
            if not selected or old_files != {
                str(p.relative_to(original)): sha256(p) for p in original.rglob("*") if p.is_file()
            }:
                raise ValueError("Application update did not stage independently of running code")
            updated = check(package, output / "updated", profile, read_only_app=True)
            if not updated["passed"]:
                raise ValueError("Updated application did not launch")
            if "Downloading patched Electron" in (output / "updated/console.log").read_text():
                raise ValueError("Application upgrade downloaded Electron again")
            current = Path(
                subprocess.check_output(
                    [
                        sys.executable,
                        "-I",
                        str(package / "loader.py"),
                        "--profile",
                        str(profile),
                        "--resolve",
                    ],
                    text=True,
                ).strip()
            )
            if current != selected or updated["client_generation"] == initial["client_generation"]:
                raise ValueError("Updated application/compatibility generation did not activate")
            report = {
                "passed": True,
                "disposable_signing_key": True,
                "public_release_modified": False,
                "fresh_profile": str(profile),
                "application_before": str(original),
                "application_after": str(current),
                "python_openasar_and_asar_patches_updated": True,
                "original_payload_unchanged": True,
                "electron_downloaded_again": False,
                "initial": initial,
                "updated": updated,
            }
            write_json(output / "application-update-report.json", report)
            return report
        finally:
            subprocess.run(["gpgconf", "--homedir", temporary, "--kill", "gpg-agent"], check=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packages", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runtime-archive", type=Path, required=True)
    args = parser.parse_args()
    verify(args.packages, args.output, args.runtime_archive)
