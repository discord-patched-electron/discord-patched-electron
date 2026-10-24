"""Guard relocation and distro metadata without downloading a runtime."""

import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import ROOT
from package_release import metadata, normalize_permissions, tarball
from prepare_app import patch_bundle


class PortableReleases(unittest.TestCase):
    def test_bootstrap_uses_launch_root_even_for_equicord_copies(self):
        source = (
            'resourcesPath=path_1.default.join(require.main.filename,"..",".."),'
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
            (app / "runtime").mkdir(parents=True)
            runtime = app / "runtime/electron"
            runtime.write_text(
                '#!/usr/bin/env python3\nimport json, os, sys\nprint(json.dumps([os.environ["DPE_APP_ROOT"], sys.argv[1]]))\n'
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
                    [str(command)],
                    text=True,
                    env={**os.environ, "DPE_CAMERA_FAST_PROBE": "0", "DPE_EQUICORD": "0"},
                )
            )
            self.assertEqual(result, [str(app), str(app / "resources/app.asar")])

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

    def test_channels_are_independent_and_dependencies_are_distro_specific(self):
        names = set()
        for channel in ("stable", "ptb", "canary"):
            config = metadata(Path("/tmp/app"), "20261024.120000", channel, 1234567890)
            names.add(config["name"])
            self.assertEqual(config["version_schema"], "none")
            self.assertIn("pipewire-bin", config["overrides"]["deb"]["depends"])
            self.assertIn("pipewire-utils", config["overrides"]["rpm"]["depends"])
            self.assertTrue(config["contents"][0]["dst"].startswith("/opt/"))
        self.assertEqual(len(names), 3)
        with self.assertRaises(ValueError):
            metadata(Path("/tmp/app"), "../../unsafe", "canary", 0)
