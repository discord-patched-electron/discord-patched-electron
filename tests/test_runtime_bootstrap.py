"""Bootstrap without Electron, and cancel a first-run helper safely."""

import os
import signal
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration/updater"))
import bootstrap
import bootstrap_dialog


class RuntimeBootstrap(unittest.TestCase):
    def test_bootstrap_errors_use_the_launcher_identity(self):
        for identity in ("discord", "discord-ptb", "discord-canary"):
            with patch.dict(
                os.environ,
                {"CHROME_DESKTOP": identity + ".desktop", "DPE_APP_ROOT": "/portable app"},
            ):
                command = bootstrap_dialog.zenity_command("--error", "--no-markup")
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

    def test_runtime_setup_with_a_display_starts_only_the_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary)
            helper = app / "integration/updater/runtime.py"
            helper.parent.mkdir(parents=True)
            helper.write_text("print('/cached/electron')\n")
            with (
                patch.object(bootstrap, "ensure_runtime", side_effect=ValueError("no cache")),
                patch.dict(os.environ, {"DPE_CLIENT_OFFLINE": "0", "DISPLAY": ":fixture"}),
                patch.object(
                    bootstrap.subprocess, "Popen", wraps=bootstrap.subprocess.Popen
                ) as launch,
            ):
                self.assertEqual(bootstrap.prepare(app, app / "profile"), "/cached/electron")
            self.assertEqual(launch.call_count, 1)
            self.assertIn(str(helper), launch.call_args.args[0])

    def test_runtime_setup_stops_its_worker_when_the_launcher_is_terminated(self):
        import shutil
        import subprocess

        updater = Path(bootstrap.__file__).parent
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary)
            fixture = app / "integration/updater"
            fixture.mkdir(parents=True)
            for name in ("bootstrap.py", "bootstrap_dialog.py", "diagnostic_log.py"):
                shutil.copyfile(updater / name, fixture / name)
            (fixture / "runtime.py").write_text(
                "import os, pathlib, signal, sys\n"
                "FAILURES = (OSError, ValueError)\n"
                "def ensure_runtime(*_args, **_kwargs):\n"
                "    raise ValueError('no cached runtime')\n"
                "if __name__ == '__main__':\n"
                "    pathlib.Path(sys.argv[1], 'helper.pid').write_text(str(os.getpid()))\n"
                "    signal.pause()\n"
            )
            process = subprocess.Popen(
                [sys.executable, str(fixture / "bootstrap.py"), str(app), str(app / "profile")],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={**os.environ, "DPE_CLIENT_OFFLINE": "0"},
                text=True,
            )
            marker = app / "helper.pid"
            try:
                deadline = time.monotonic() + 5
                while not marker.exists() or not marker.read_text():
                    if process.poll() is not None or time.monotonic() >= deadline:
                        self.fail("Runtime helper did not start")
                    time.sleep(0.01)
                process.terminate()
                _, stderr = process.communicate(timeout=5)
                self.assertNotEqual(process.returncode, 0)
                self.assertIn("cancelled", stderr)
                with self.assertRaises(ProcessLookupError):
                    os.kill(int(marker.read_text()), 0)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()

    def test_setup_completion_stops_descendants_even_after_the_leader_exits(self):
        def running(pid):
            try:
                return Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1][0] != "Z"
            except (FileNotFoundError, ProcessLookupError):
                # procfs can report ESRCH if the process exits after open()
                # but before read(), rather than ENOENT at open().
                return False

        worker = (
            "import os, pathlib, signal, sys, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))\n"
            "time.sleep(10)\n"
        )
        for outcome in ("failure", "success"):
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
                        "raise SystemExit('synthetic setup failure')\n"
                        if outcome == "failure"
                        else "print('/cached/electron')\n"
                    )
                )
                try:
                    with (
                        patch.object(
                            bootstrap, "ensure_runtime", side_effect=ValueError("no cache")
                        ),
                        patch.dict(
                            os.environ,
                            {
                                "DPE_CLIENT_OFFLINE": "0",
                                "DISPLAY": "",
                                "WAYLAND_DISPLAY": "",
                            },
                        ),
                    ):
                        if outcome == "success":
                            self.assertEqual(
                                bootstrap.prepare(app, app / "profile"), "/cached/electron"
                            )
                        else:
                            with self.assertRaises(RuntimeError):
                                bootstrap.prepare(app, app / "profile")
                    pid = int(pid_file.read_text())
                    deadline = time.monotonic() + 1
                    while running(pid) and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertFalse(running(pid), "setup left its private-output child running")
                finally:
                    if pid_file.exists() and running(pid := int(pid_file.read_text())):
                        try:
                            os.kill(pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass  # It exited between the status read and kill.
