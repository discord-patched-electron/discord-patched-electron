"""Bootstrap without Electron, and cancel a first-run helper safely."""

import os
import signal
import sys
import tempfile
import time
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

    def test_quick_setup_with_a_display_does_not_open_the_native_dialog(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary)
            helper = app / "integration/updater/runtime.py"
            helper.parent.mkdir(parents=True)
            helper.write_text("print('/cached/electron')\n")
            with (
                patch.object(bootstrap, "ensure_runtime", side_effect=ValueError("no cache")),
                patch.dict(os.environ, {"DPE_CLIENT_OFFLINE": "0", "DISPLAY": ":fixture"}),
                patch.object(bootstrap.time, "monotonic", return_value=0),
                patch.object(
                    bootstrap.subprocess, "Popen", wraps=bootstrap.subprocess.Popen
                ) as launch,
            ):
                self.assertEqual(bootstrap.prepare(app, app / "profile"), "/cached/electron")
            self.assertEqual(launch.call_count, 1)
            self.assertIn(str(helper), launch.call_args.args[0])

    def test_slow_setup_opens_progress_after_the_delay_and_closes_it_on_completion(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary)
            helper = app / "integration/updater/runtime.py"
            helper.parent.mkdir(parents=True)
            helper.write_text(
                "import pathlib, sys, time\n"
                "marker = pathlib.Path(sys.argv[1], 'dialog.json')\n"
                "for _ in range(100):\n"
                " if marker.exists(): break\n"
                " time.sleep(.01)\n"
                "print('/cached/electron')\n"
            )
            helper.with_name("bootstrap_dialog.py").write_text(
                "import json, os, pathlib, sys, time\n"
                "pathlib.Path(__file__).parents[2].joinpath('dialog.json').write_text("
                "json.dumps({'time': time.monotonic(), 'pid': os.getpid()}))\n"
                "sys.stdin.read()\n"
            )
            with (
                patch.object(bootstrap, "ensure_runtime", side_effect=ValueError("no cache")),
                patch.dict(os.environ, {"DPE_CLIENT_OFFLINE": "0", "DISPLAY": ":fixture"}),
                patch.object(bootstrap, "DIALOG_DELAY_SECONDS", 0.1),
            ):
                started = time.monotonic()
                self.assertEqual(bootstrap.prepare(app, app / "profile"), "/cached/electron")
            import json

            dialog = json.loads((app / "dialog.json").read_text())
            self.assertGreaterEqual(dialog["time"] - started, 0.1)
            with self.assertRaises(ProcessLookupError):
                os.kill(dialog["pid"], 0)

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
                patch.object(bootstrap, "DIALOG_DELAY_SECONDS", 0.1),
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

    def test_native_cancellation_wins_when_the_helper_finishes_between_polls(self):
        task = Mock(pid=123, returncode=0)
        task.poll.side_effect = [None, 0]
        dialog = Mock()
        # The first sample sees a live dialog. It closes while the helper
        # finishes, before the next iteration can enter its cancellation check.
        dialog.poll.side_effect = [None, 1, 1]

        def launch(command, **options):
            if "--bootstrap" in command:
                options["stdout"].write("/cached/electron\n")
                return task
            return dialog

        with (
            patch.object(bootstrap, "ensure_runtime", side_effect=ValueError("no cache")),
            patch.object(bootstrap.subprocess, "Popen", side_effect=launch),
            patch.object(bootstrap.os, "killpg"),
            patch.object(bootstrap.time, "sleep"),
            patch.object(bootstrap, "DIALOG_DELAY_SECONDS", 0),
            patch.dict(os.environ, {"DPE_CLIENT_OFFLINE": "0", "DISPLAY": ":fixture"}),
            self.assertRaisesRegex(InterruptedError, "cancelled"),
        ):
            bootstrap.prepare(Path("/fixture"), Path("/fixture/profile"))
        dialog.stdin.close.assert_called_once()

    def test_setup_completion_stops_descendants_even_after_the_leader_exits(self):
        def running(pid):
            try:
                return Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1][0] != "Z"
            except FileNotFoundError:
                return False

        worker = (
            "import os, pathlib, signal, sys, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))\n"
            "time.sleep(10)\n"
        )
        for outcome in ("cancel", "failure", "success"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as temporary:
                app = Path(temporary)
                pid_file = app / "worker.pid"
                helper = app / "integration/updater/runtime.py"
                helper.parent.mkdir(parents=True)
                helper.write_text(
                    "import pathlib, subprocess, sys, time\n"
                    "pid = pathlib.Path(sys.argv[1], 'worker.pid')\n"
                    f"subprocess.Popen([sys.executable, '-c', {worker!r}, str(pid)], "
                    "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
                    "for _ in range(200):\n"
                    " if pid.exists(): break\n"
                    " time.sleep(.01)\n"
                    + (
                        "time.sleep(10)\n"
                        if outcome == "cancel"
                        else "raise SystemExit('synthetic setup failure')\n"
                        if outcome == "failure"
                        else "print('/cached/electron')\n"
                    )
                )
                helper.with_name("bootstrap_dialog.py").write_text(
                    "import pathlib, time\n"
                    "pid = pathlib.Path(__file__).parents[2] / 'worker.pid'\n"
                    "for _ in range(200):\n"
                    " if pid.exists(): break\n"
                    " time.sleep(.01)\n"
                    "raise SystemExit(1)\n"
                )
                try:
                    with (
                        patch.object(
                            bootstrap, "ensure_runtime", side_effect=ValueError("no cache")
                        ),
                        patch.object(bootstrap, "DIALOG_DELAY_SECONDS", 0),
                        patch.dict(
                            os.environ,
                            {
                                "DPE_CLIENT_OFFLINE": "0",
                                "DISPLAY": ":fixture" if outcome == "cancel" else "",
                                "WAYLAND_DISPLAY": "",
                            },
                        ),
                    ):
                        if outcome == "success":
                            self.assertEqual(
                                bootstrap.prepare(app, app / "profile"), "/cached/electron"
                            )
                        else:
                            error = InterruptedError if outcome == "cancel" else RuntimeError
                            with self.assertRaises(error):
                                bootstrap.prepare(app, app / "profile")
                    pid = int(pid_file.read_text())
                    deadline = time.monotonic() + 1
                    while running(pid) and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertFalse(running(pid), "setup left its private-output child running")
                finally:
                    if pid_file.exists() and running(pid := int(pid_file.read_text())):
                        os.kill(pid, signal.SIGKILL)
