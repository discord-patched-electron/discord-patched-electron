"""Guard relocation and distro metadata without downloading a runtime."""

import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import ROOT, read_json, sha256, write_json
from package_release import metadata, normalize_permissions, package, tarball
from prepare_app import patch_bundle


class PortableReleases(unittest.TestCase):
    def test_release_packages_share_a_runtime_free_payload_and_retain_the_verified_app(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app, output = root / "verified app", root / "packages"
            (app / "runtime").mkdir(parents=True)
            (app / "resources").mkdir()
            (app / "runtime/electron").write_bytes(b"large verified runtime fixture")
            (app / "runtime/resources").symlink_to("../resources")
            (app / "resources/app.asar").write_bytes(b"project loader")
            (app / "launch.sh").write_text("#!/bin/sh\n")
            (app / "launch.sh").chmod(0o755)
            (app / "discord-canary.desktop").write_text("[Desktop Entry]\n")
            (app / "discord.png").write_bytes(b"icon")
            write_json(app / "client-update.json", {"modules": {}})
            write_json(
                app / "client-distribution.json",
                {
                    "mode": "module-only",
                    "bundled_discord_client": False,
                    "loader_sha256": sha256(app / "resources/app.asar"),
                },
            )
            capacity = {
                "compiled_version": "46.0.0-alpha.1",
                "runtime_key": "a" * 64,
                "archive_sha256": "b" * 64,
                "source": {"target": "prerelease"},
            }
            policy = {
                "channel": "canary",
                "electron_version": capacity["compiled_version"],
                "runtime_key": capacity["runtime_key"],
                "archive_sha256": capacity["archive_sha256"],
                "release_version": None,
            }
            write_json(app / "runtime-update.json", policy)
            write_json(
                app / "local-build.json",
                {
                    "portable": True,
                    "runtime_kind": "compiled",
                    "launch_verified": True,
                    "client_pins_verified": True,
                    "client_manifest_sha256": sha256(app / "client-update.json"),
                    "executable_sha256": sha256(app / "runtime/electron"),
                    "capacity": capacity,
                    "channel": "canary",
                    "client_version": "1.0.123",
                },
            )

            def nfpm(command, **_kwargs):
                config = read_json(Path(command[command.index("--config") + 1]))
                payload = Path(config["contents"][0]["src"])
                self.assertFalse((payload / "runtime").exists())
                self.assertTrue((payload / "loader.py").is_file())
                self.assertFalse((payload / "resources").exists())
                self.assertFalse((payload / "integration").exists())
                Path(command[command.index("--target") + 1]).write_bytes(b"package fixture")

            with (
                patch("package_release.validate_capacity"),
                patch("package_release.subprocess.run", side_effect=nfpm) as packager,
                patch(
                    "package_release.subprocess.check_output",
                    side_effect=["dirty", "fixture-commit"],
                ),
            ):
                package(app, output, "20261026.120000", 1234567890, "nfpm")
            self.assertEqual(packager.call_count, 3)
            with tarfile.open(next(output.glob("*.tar.gz"))) as archive:
                names = archive.getnames()
                self.assertFalse(
                    any("/runtime/" in name or name.endswith("/runtime") for name in names)
                )
                checksums = archive.extractfile(names[0] + "/SHA256SUMS").read().decode()
                self.assertNotIn("runtime/", checksums)
                self.assertIn("bootstrap.json", checksums)
                self.assertNotIn("runtime-update.json", checksums)
            self.assertFalse(read_json(output / "release.json")["bundled_electron"])
            self.assertEqual(read_json(app / "runtime-update.json"), policy)
            self.assertEqual(
                (app / "runtime/electron").read_bytes(), b"large verified runtime fixture"
            )

    def test_arch_conflicts_cover_canary_providers_without_blocking_other_clients(self):
        config = metadata(Path("/unused"), "20261024.202623", "canary", 0)
        self.assertEqual(
            config["overrides"]["archlinux"]["conflicts"],
            ["discord-canary", "discord-canary-electron-bin"],
        )
        self.assertEqual(config["conflicts"], ["discord-canary"])

    def test_release_installers_register_the_same_desktop_entry_and_icon(self):
        config = metadata(Path("/app"), "20261024.202623", "canary", 0)
        contents = {entry["dst"]: entry["src"] for entry in config["contents"]}
        self.assertEqual(
            contents["/usr/share/applications/discord-canary.desktop"],
            "/app/discord-canary.desktop",
        )
        self.assertEqual(
            contents["/usr/share/icons/hicolor/256x256/apps/discord-canary.png"],
            "/app/discord.png",
        )

    def test_bootstrap_uses_launch_root_even_for_equicord_copies(self):
        source = (
            'resourcesPath=path_1.default.join(require.main.filename,"..",".."),'
            'userDataPath=path_1.default.join(userDataRoot,"discord"+("stable"===buildInfo.releaseChannel?"":buildInfo.releaseChannel));'
            'process.resourcesPath;"build_info.json")),disableUpdater:'
            'skipHostUpdate=settings?.get("SKIP_HOST_UPDATE")||!hostUpdatable;'
            "Exec=${exePath};Name=${appName};Icon=${iconPath}"
        )
        patched = patch_bundle(source, "package", Path("/obsolete/staging/path"), portable=True)
        self.assertNotIn("/obsolete/staging/path", patched)
        self.assertIn('localModulesRoot:process.env.DPE_APP_ROOT+"/modules"', patched)
        self.assertIn('(process.env.DPE_APP_ROOT+"/resources")', patched)
        with self.assertRaisesRegex(ValueError, "Unsupported bootstrap"):
            patch_bundle(
                source.replace('"build_info.json"', '"unknown.json"'), "package", portable=True
            )

    def test_launcher_resolves_symlinks_and_spaces_after_moving(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            app = base / "moved app"
            shutil.copytree(ROOT / "integration/launcher", app / "integration/launcher")
            (app / "runtime").mkdir(parents=True)
            runtime = app / "runtime/electron"
            runtime.write_text(
                '#!/usr/bin/env python3\nimport json, os, sys\nprint(json.dumps([os.environ["DPE_APP_ROOT"], sys.argv[1:]]))\n'
            )
            runtime.chmod(0o755)
            name = "discord-canary-prerelease-patched-electron-bin"
            launcher = (ROOT / "integration/launcher/discord-launcher.sh").read_text()
            launcher = launcher.replace("@PKGNAME@", name).replace("@DESKTOP_ID@", "test.app")
            launcher = launcher.replace(
                "root=/usr/lib/" + name,
                'root=$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")',
            )
            (app / "launch.sh").write_text(launcher)
            (app / "launch.sh").chmod(0o755)
            command = base / "command"
            command.symlink_to(app / "launch.sh")
            result = json.loads(
                subprocess.check_output(
                    [str(command), "--ozone-platform=x11"],
                    text=True,
                    env={**os.environ, "DPE_CAMERA_FAST_PROBE": "0", "DPE_DEFAULT_FLAGS": "0"},
                )
            )
            self.assertEqual(result, [str(app), ["--class=test.app", "--ozone-platform=x11"]])

    def test_tarball_is_reproducible_and_removes_privileged_bits(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = root / "app"
            app.mkdir()
            (app / "launch.sh").write_text("#!/bin/sh\n")
            (app / "launch.sh").chmod(0o4755)
            for name in ("first.tar.gz", "second.tar.gz"):
                tarball(app, root / name, "discord", 1234567890)
            self.assertEqual(
                (root / "first.tar.gz").read_bytes(), (root / "second.tar.gz").read_bytes()
            )
            with tarfile.open(root / "first.tar.gz") as archive:
                launcher = archive.getmember("discord/launch.sh")
                self.assertEqual((launcher.uid, launcher.gid, launcher.mode), (0, 0, 0o755))

    def test_system_install_files_are_readable_without_privileged_modes(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary)
            info = app / "build_info.json"
            info.write_text("{}")
            info.chmod(0o600)
            executable = app / "chrome-sandbox"
            executable.write_text("fixture")
            executable.chmod(0o4755)
            normalize_permissions(app)
            self.assertEqual(info.stat().st_mode & 0o7777, 0o644)
            self.assertEqual(executable.stat().st_mode & 0o7777, 0o755)
            (app / "outside").symlink_to("/etc/passwd")
            with self.assertRaisesRegex(ValueError, "Symlink leaves"):
                normalize_permissions(app)

    def test_release_rejects_an_installed_mod_before_creating_packages(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary) / "app"
            (app / "resources").mkdir(parents=True)
            (app / "resources/_app.asar").write_bytes(b"original bootstrap")
            (app / "local-build.json").write_text(
                json.dumps(
                    {
                        "portable": True,
                        "runtime_kind": "compiled",
                        "launch_verified": True,
                    }
                )
            )
            output = Path(temporary) / "packages"
            with self.assertRaisesRegex(ValueError, "Uninstall client mods"):
                package(app, output, "20261024.120000", 1234567890, "nfpm")
            self.assertFalse(output.exists())

    def test_only_canary_is_enabled_and_dependencies_are_distro_specific(self):
        names = set()
        for channel in ("canary",):
            config = metadata(Path("/tmp/app"), "20261024.120000", channel, 1234567890)
            names.add(config["name"])
            self.assertEqual(config["version_schema"], "none")
            self.assertIn("pipewire-bin", config["overrides"]["deb"]["depends"])
            self.assertIn("pipewire-utils", config["overrides"]["rpm"]["depends"])
            self.assertIn("pipewire", config["overrides"]["archlinux"]["depends"])
            self.assertIn("python>=3.11", config["overrides"]["archlinux"]["depends"])
            self.assertIn("python3 (>= 3.11)", config["overrides"]["deb"]["depends"])
            self.assertIn("python3 >= 3.11", config["overrides"]["rpm"]["depends"])
            self.assertIn("gnupg", config["overrides"]["archlinux"]["depends"])
            self.assertIn("gnupg2", config["overrides"]["rpm"]["depends"])
            self.assertTrue(
                {"gpg", "gpgv", "apparmor"}.issubset(config["overrides"]["deb"]["depends"])
            )
            self.assertTrue(config["contents"][0]["dst"].startswith("/opt/"))
        self.assertEqual(len(names), 1)
        for disabled in ("stable", "ptb"):
            with self.assertRaisesRegex(ValueError, "Disabled Discord channel"):
                metadata(Path("/tmp/app"), "20261024.120000", disabled, 0)
        with self.assertRaises(ValueError):
            metadata(Path("/tmp/app"), "../../unsafe", "canary", 0)

    def test_release_requires_launch_verified_unchanged_module_pins(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary) / "app"
            app.mkdir()
            pins = app / "client-update.json"
            pins.write_text('{"modules": {"fixture": "tested"}}')
            build = {
                "portable": True,
                "runtime_kind": "compiled",
                "launch_verified": True,
                "client_manifest_sha256": sha256(pins),
            }
            output = Path(temporary) / "packages"
            for changed, message in (
                (False, "Validate the package's pinned Discord modules"),
                (True, "manifest changed after launch validation"),
            ):
                with self.subTest(changed=changed):
                    if changed:
                        build["client_pins_verified"] = True
                        pins.write_text('{"modules": {"fixture": "untested"}}')
                    (app / "local-build.json").write_text(json.dumps(build))
                    with self.assertRaisesRegex(ValueError, message):
                        package(app, output, "20261024.120000", 1234567890, "nfpm")
                    self.assertFalse(output.exists())
