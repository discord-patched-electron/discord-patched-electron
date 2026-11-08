"""AppImage wrapping preserves bootstrap identity and fails closed."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from appimage import build, stage, validate_dependencies
from common import ROOT, read_json, sha256, write_json
from desktop_identity import desktop_id


class AppImages(unittest.TestCase):
    def test_wrapping_preserves_all_payload_bytes_and_channel_identity(self):
        for channel in ("canary", "ptb", "stable"):
            with self.subTest(channel=channel), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                app, appdir = root / "payload", root / "DPE.AppDir"
                app.mkdir()
                desktop = f"{desktop_id(channel)}.desktop"
                (app / desktop).write_text(
                    "[Desktop Entry]\nExec=/opt/old/launch.sh %U\nIcon=old\n"
                    "TryExec=/opt/old/launch.sh\nPath=/opt/old\n"
                )
                (app / "discord.png").write_bytes(b"icon")
                (app / "launch.sh").write_text("#!/bin/sh\nexit 0\n")
                before = {p.name: sha256(p) for p in app.iterdir()}
                stage(app, appdir, channel)
                self.assertEqual(before, {p.name: sha256(p) for p in app.iterdir()})
                self.assertEqual(
                    before, {p.name: sha256(p) for p in (appdir / "usr/lib/dpe").iterdir()}
                )
                self.assertEqual((appdir / ".DirIcon").read_bytes(), b"icon")
                text = (appdir / desktop).read_text()
                self.assertIn("Exec=AppRun %U", text)
                self.assertIn(f"Icon={desktop_id(channel)}", text)
                self.assertNotIn("/opt/old", text)

    def test_missing_builder_or_runtime_fails_instead_of_omitting_asset(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for installed in (False, True):
                if installed:
                    (root / "appimagetool").touch()
                with self.assertRaisesRegex(ValueError, "Install pinned"):
                    build(root / "app", root / "out.AppImage", 0, "canary", root)
                self.assertFalse((root / "out.AppImage").exists())

    def test_dependency_policy_rejects_stale_recipes_and_host_stack(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata = {
                "schema": 2,
                "recipe_sha256": sha256(ROOT / "scripts/stage_appimage_dependencies.sh"),
                "container_sha256": sha256(ROOT / "ci/appimage/Dockerfile"),
                "tools": read_json(ROOT / "config/appimage-tools.json"),
            }
            manifest = root / "usr/share/dpe-dependencies/build.json"
            write_json(manifest, metadata)
            validate_dependencies(root)
            for name in (
                "usr/lib/libpipewire-0.3.so.0",
                "usr/lib/spa-0.2",
                "usr/desktop/usr/lib/libgtk-3.so.0",
                "usr/lib/libnss3.so",
                "usr/lib/libpulse.so.0",
                "usr/lib/libc.so.6",
                "usr/bin/pw-dump",
                "usr/bin/zenity",
                "usr/lib/libgbm.so.1",
            ):
                with self.subTest(name=name):
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.touch()
                    with self.assertRaisesRegex(ValueError, "Host-owned"):
                        validate_dependencies(root)
                    path.unlink()
                    parent = path.parent
                    while parent != root and not any(parent.iterdir()):
                        parent.rmdir()
                        parent = parent.parent
            metadata["schema"] = 1
            write_json(manifest, metadata)
            with self.assertRaisesRegex(ValueError, "Stale"):
                validate_dependencies(root)

    def test_apprun_preserves_host_environment_arguments_and_exit_status(self):
        names = (
            "LD_LIBRARY_PATH",
            "SPA_PLUGIN_DIR",
            "PIPEWIRE_MODULE_DIR",
            "PIPEWIRE_CONFIG_DIR",
            "XDG_DATA_DIRS",
            "SSL_CERT_FILE",
        )
        with tempfile.TemporaryDirectory(prefix="appimage with spaces ") as temporary:
            root = Path(temporary)
            shutil.copy2(ROOT / "packaging/linux/appimage-apprun.sh", root / "AppRun")
            launch = root / "usr/lib/dpe/launch.sh"
            launch.parent.mkdir(parents=True)
            launch.write_text(
                f"#!{sys.executable}\nimport json, os, sys\n"
                "print(json.dumps([dict(os.environ), sys.argv[1:]]))\nsys.exit(23)\n"
            )
            launch.chmod(0o755)
            arguments = ["argument with spaces", "discord://test?one=two&three=four"]
            for custom in (False, True):
                environment = {k: v for k, v in os.environ.items() if k not in names}
                if custom:
                    environment.update({name: "/host/custom/" + name for name in names})
                result = subprocess.run(
                    ["sh", str(root / "AppRun"), *arguments],
                    env=environment,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 23, result.stderr)
                actual, forwarded = json.loads(result.stdout)
                self.assertEqual(forwarded, arguments)
                for name in names:
                    if name == "SSL_CERT_FILE" and not custom:
                        continue
                    self.assertEqual(actual.get(name), environment.get(name), name)
