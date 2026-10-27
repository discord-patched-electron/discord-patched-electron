"""Prepare Electron before its startup window is available."""

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from bootstrap_dialog import zenity_command
from runtime import FAILURES, ensure_runtime


def prepare(app: Path, profile: Path) -> str:
    try:
        return str(ensure_runtime(app, profile, offline=True, bootstrap=True))
    except FAILURES:
        if os.environ.get("DPE_CLIENT_OFFLINE") == "1":
            raise
    # Most launches return above. Only the initial runtime download needs a
    # native dialog; the normal Electron window handles subsequent updates.
    dialog = None
    command = [
        sys.executable,
        str(app / "integration/updater/runtime.py"),
        str(app),
        str(profile),
        "--bootstrap",
    ]
    with tempfile.TemporaryFile(mode="w+") as errors, tempfile.TemporaryFile(mode="w+") as result:
        task = subprocess.Popen(command, stdout=result, stderr=errors, start_new_session=True)
        try:
            if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
                try:
                    dialog = subprocess.Popen(
                        [
                            sys.executable,
                            str(app / "integration/updater/bootstrap_dialog.py"),
                        ],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                except OSError:
                    pass
            while task.poll() is None:
                if dialog and dialog.poll() == 1:
                    raise InterruptedError("Electron setup cancelled")
                time.sleep(0.1)
            errors.seek(0)
            details = errors.read()
            if details:
                print(details, file=sys.stderr, end="")
            if task.returncode:
                raise RuntimeError(
                    details.strip() or "Electron setup failed; retry with internet access"
                )
            result.seek(0)
            return result.read().strip()
        finally:
            if task.poll() is None:
                os.killpg(task.pid, signal.SIGTERM)
                try:
                    task.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(task.pid, signal.SIGKILL)
                    task.wait()
            if dialog:
                dialog.stdin.close()
                if dialog.poll() is None:
                    dialog.terminate()
                try:
                    dialog.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    dialog.kill()
                    dialog.wait()


def main():
    # Cancellation always tears down the helper group, including probes.
    def interrupted(_signal, _frame):
        raise InterruptedError("Electron setup cancelled")

    for name in (signal.SIGINT, signal.SIGTERM):
        signal.signal(name, interrupted)
    try:
        print(prepare(Path(sys.argv[1]), Path(sys.argv[2])))
    except (*FAILURES, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        if (
            not isinstance(error, InterruptedError)
            and shutil.which("zenity")
            and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
        ):
            subprocess.run(
                zenity_command(
                    "--error",
                    "--no-markup",
                    "--title=Discord Patched Electron",
                    "--text=" + str(error)[-4000:],
                ),
                executable="zenity",
                check=False,
            )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
