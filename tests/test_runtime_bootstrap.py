"""Bootstrap without Electron, and cancel a first-run helper safely."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration/updater"))
import bootstrap


class RuntimeBootstrap(unittest.TestCase):
    def test_cached_runtime_starts_without_a_dialog_or_network_helper(self):
        with (
            patch.object(
                bootstrap, "ensure_runtime", return_value=Path("/cached/electron")
            ) as ensure,
            patch.object(bootstrap.subprocess, "Popen") as launch,
        ):
            self.assertEqual(bootstrap.prepare(Path("/app"), Path("/profile")), "/cached/electron")
            ensure.assert_called_once_with(
                Path("/app"), Path("/profile"), offline=True, bootstrap=True
            )
            launch.assert_not_called()

    def test_missing_runtime_offline_fails_without_starting_a_download(self):
        with (
            patch.object(bootstrap, "ensure_runtime", side_effect=ValueError("no cached runtime")),
            patch.object(bootstrap.subprocess, "Popen") as launch,
            patch.dict(os.environ, {"DPE_CLIENT_OFFLINE": "1"}),
        ):
            with self.assertRaisesRegex(ValueError, "no cached runtime"):
                bootstrap.prepare(Path("/app"), Path("/profile"))
            launch.assert_not_called()

    def test_headless_first_setup_returns_only_the_runtime_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary)
            helper = app / "integration/updater/runtime.py"
            helper.parent.mkdir(parents=True)
            helper.write_text(
                "import sys\nprint('download diagnostic', file=sys.stderr)\nprint('/profile/cached runtime/electron')\n"
            )
            with (
                patch.object(bootstrap, "ensure_runtime", side_effect=ValueError("no cache")),
                patch.dict(
                    os.environ,
                    {"DPE_CLIENT_OFFLINE": "0", "DISPLAY": "", "WAYLAND_DISPLAY": ""},
                ),
            ):
                self.assertEqual(
                    bootstrap.prepare(app, app / "profile"), "/profile/cached runtime/electron"
                )
                helper.write_text("raise SystemExit('verification failed')\n")
                with self.assertRaisesRegex(RuntimeError, "verification failed"):
                    bootstrap.prepare(app, app / "profile")

    def test_cancelled_native_dialog_terminates_and_reaps_the_runtime_helper(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary)
            helper = app / "integration/updater/runtime.py"
            helper.parent.mkdir(parents=True)
            helper.write_text(
                "import os, pathlib, sys, time\npathlib.Path(sys.argv[1], 'helper.pid').write_text(str(os.getpid()))\ntime.sleep(30)\n"
            )
            dialog = helper.with_name("bootstrap_dialog.py")
            dialog.write_text(
                "import pathlib, time\npid = pathlib.Path(__file__).parents[2] / 'helper.pid'\nfor _ in range(200):\n if pid.exists(): break\n time.sleep(.01)\nraise SystemExit(1)\n"
            )
            with (
                patch.object(bootstrap, "ensure_runtime", side_effect=ValueError("no cache")),
                patch.dict(
                    os.environ,
                    {
                        "DPE_CLIENT_OFFLINE": "0",
                        "DISPLAY": ":fixture",
                    },
                ),
            ):
                with self.assertRaisesRegex(InterruptedError, "cancelled"):
                    bootstrap.prepare(app, app / "profile")
            pid = int((app / "helper.pid").read_text())
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
