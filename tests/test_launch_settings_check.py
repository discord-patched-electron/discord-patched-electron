"""The settings fixture must never pack through a verified runtime's symlink."""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_launch_settings


class SettingsRuntimeCopy(unittest.TestCase):
    def test_absolute_resources_link_is_replaced_without_touching_its_target(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            original = root / "verified"
            original.mkdir()
            (original / "app.asar").write_bytes(b"verified original application")
            runtime = root / "runtime"
            runtime.mkdir()
            executable = runtime / "electron"
            executable.write_bytes(b"fixture executable")
            (runtime / "resources").symlink_to(original, target_is_directory=True)
            output = root / "test"

            def command(*args):
                if args[0] == "cp":
                    shutil.copytree(args[-2], args[-1], symlinks=True)
                else:
                    target = Path(args[-1])
                    self.assertFalse(target.parent.is_symlink())
                    target.write_bytes(b"isolated settings fixture")

            def execute(*args, **kwargs):
                (output / "settings.json").write_text(json.dumps({"passed": True}))
                return SimpleNamespace(returncode=0)

            with (
                patch.object(check_launch_settings, "build_ui"),
                patch.object(check_launch_settings, "run", side_effect=command),
                patch.object(check_launch_settings.subprocess, "run", side_effect=execute),
            ):
                self.assertTrue(check_launch_settings.check(executable, output)["passed"])
            self.assertEqual((original / "app.asar").read_bytes(), b"verified original application")
            self.assertEqual(
                (output / "runtime/resources/app.asar").read_bytes(), b"isolated settings fixture"
            )
