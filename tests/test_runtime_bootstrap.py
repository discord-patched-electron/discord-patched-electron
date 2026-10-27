"""Bootstrap without Electron, and cancel a first-run helper safely."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration/updater"))
import bootstrap
import bootstrap_dialog


class RuntimeBootstrap(unittest.TestCase):
    def test_native_dialog_sets_desktop_identity_before_creating_windows(self):
        calls = Mock()
        libraries = {name: Mock() for name in ("gtk", "gdk", "gdk_pixbuf", "glib", "gobject")}
        for name, library in libraries.items():
            calls.attach_mock(library, name)
        libraries["gtk"].gtk_dialog_run.return_value = 0
        with (
            patch.object(
                bootstrap_dialog.c,
                "CDLL",
                side_effect=lambda name: libraries[name.removeprefix("lib").split("-")[0]],
            ),
            patch.dict(
                os.environ,
                {"CHROME_DESKTOP": "discord-canary.desktop", "DPE_APP_ROOT": "/portable app"},
            ),
        ):
            self.assertEqual(bootstrap_dialog.show(preview=True), 0)
        names = [call[0] for call in calls.mock_calls]
        self.assertLess(names.index("glib.g_set_prgname"), names.index("gtk.gtk_init_check"))
        self.assertLess(names.index("gtk.gtk_init_check"), names.index("gdk.gdk_set_program_class"))
        self.assertLess(
            names.index("gdk.gdk_set_program_class"), names.index("gtk.gtk_builder_new")
        )
        libraries["glib"].g_set_prgname.assert_called_once_with(b"discord-canary")
        libraries["gdk"].gdk_set_program_class.assert_called_once_with(b"discord-canary")
        libraries["gtk"].gtk_window_set_default_icon_name.assert_called_once_with(b"discord-canary")
        libraries["gdk_pixbuf"].gdk_pixbuf_new_from_file_at_scale.assert_called_once_with(
            b"/portable app/discord.png", 128, 128, 1, None
        )
        icon = libraries["gdk_pixbuf"].gdk_pixbuf_new_from_file_at_scale.return_value
        libraries["gtk"].gtk_window_set_default_icon.assert_called_once_with(icon)
        libraries["gobject"].g_object_unref.assert_any_call(icon)

    def test_zenity_fallback_and_error_use_the_launcher_identity(self):
        for identity in ("discord", "discord-ptb", "discord-canary"):
            with patch.dict(
                os.environ,
                {"CHROME_DESKTOP": identity + ".desktop", "DPE_APP_ROOT": "/portable app"},
            ):
                command = bootstrap_dialog.zenity_command("--progress", "--pulsate")
                self.assertEqual(command[0], identity)
                self.assertIn("--window-icon=/portable app/discord.png", command)
                with (
                    patch.object(bootstrap, "prepare", side_effect=ValueError("setup failed")),
                    patch.object(bootstrap.signal, "signal"),
                    patch.object(sys, "argv", ["bootstrap", "/app", "/profile"]),
                    patch.object(bootstrap.shutil, "which", return_value="/usr/bin/zenity"),
                    patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-fixture"}),
                    patch.object(bootstrap.subprocess, "run") as run,
                    self.assertRaises(SystemExit),
                ):
                    bootstrap.main()
                self.assertEqual(run.call_args.args[0][0], identity)
                self.assertIn("--error", run.call_args.args[0])
                self.assertEqual(run.call_args.kwargs["executable"], "zenity")

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
