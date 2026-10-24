"""Check native shortcut press/release delivery on a private D-Bus session.

Uses a synthetic portal: never grabs real keys or changes desktop permissions.
Run with the system Python (python3-dbus and python3-gi are required).
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from common import ROOT, read_json
from desktop_identity import desktop_id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--utils", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--inside-private-bus", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    output = args.output.resolve()
    if not args.inside_private_bus:
        if output.exists():
            parser.error("Choose a fresh output directory")
        output.mkdir(parents=True)
        subprocess.run(
            [
                "cp",
                "-a",
                "--reflink=auto",
                str(args.runtime.resolve().parent),
                str(output / "runtime"),
            ],
            check=True,
        )
        subprocess.run(
            [
                str(ROOT / "node_modules/.bin/asar"),
                "pack",
                str(ROOT / "tests/shortcuts"),
                str(output / "runtime/resources/app.asar"),
            ],
            check=True,
        )
        subprocess.run(
            [
                "dbus-run-session",
                "--",
                sys.executable,
                __file__,
                *sys.argv[1:],
                "--inside-private-bus",
            ],
            check=True,
        )
        return
    env = {
        **os.environ,
        "DPE_SHORTCUT_OUTPUT": str(output),
        "DPE_SHORTCUT_MODULE": str(args.utils.resolve()),
        "DISCORD_PORTAL_APP_ID": desktop_id("canary"),
        "XDG_SESSION_TYPE": "wayland",
        "NO_AT_BRIDGE": "1",
        "GIO_USE_VFS": "local",
    }
    env.pop("ELECTRON_RUN_AS_NODE", None)
    with (output / "portal.log").open("w") as log:
        portal = subprocess.Popen(
            [sys.executable, str(ROOT / "tests/shortcuts/portal.py")],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            for _ in range(50):
                if (output / "portal-ready").exists():
                    break
                if portal.poll() is not None:
                    raise RuntimeError("Private test portal failed")
                time.sleep(0.1)
            else:
                raise TimeoutError("Private test portal did not become ready")
            with (output / "console.log").open("w") as console:
                subprocess.run(
                    [
                        str(output / "runtime" / args.runtime.name),
                        "--ozone-platform=wayland",
                        "--disable-setuid-sandbox",
                    ],
                    env=env,
                    stdout=console,
                    stderr=subprocess.STDOUT,
                    timeout=25,
                    check=True,
                )
            result = read_json(output / "shortcuts.json")
            identity = read_json(output / "portal.json")
            if (
                result.get("passed") is not True
                or identity.get("app_id") != env["DISCORD_PORTAL_APP_ID"]
            ):
                raise ValueError("Native shortcut delivery or portal identity check failed")
            print("Native Wayland portal registration and press/release: passed")
        finally:
            portal.terminate()
            portal.wait(timeout=5)


if __name__ == "__main__":
    main()
