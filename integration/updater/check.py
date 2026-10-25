"""Prepare Electron and Discord updates without changing a running process."""

import json
import os
import sqlite3
import sys
from pathlib import Path

from client import FAILURES, ensure_client
from installation import publish
from runtime import ensure_runtime
from support import progress, read_object


def check(app: Path, client_root: Path, channel: str) -> dict:
    offline = os.environ.get("DPE_CLIENT_OFFLINE") == "1"
    progress("check", "Checking patched Electron")
    executable = ensure_runtime(app, client_root.parent, offline=offline)
    progress("check", "Checking Discord modules")
    host = ensure_client(client_root, channel, offline=offline, executable=executable)
    result = {"runtime": str(executable), "host": str(host)}
    if os.environ.get("DPE_PREPARE_ONLY") != "1":
        progress("check", "Preparing Discord")
        installed = publish(app, client_root.parent, host, executable)
        state = read_object(host / "client.json")
        revision = state["modules"]["discord_voice"]["version"]
        result["installed_runtime"] = str(installed / "electron")
        result["voice"] = str(installed / "modules" / f"discord_voice-{revision}" / "discord_voice")
    progress("ready", "Starting Discord")
    return result


def launch_paths(filename: Path):
    """Read this launch's private result; never reselect background-update pointers."""
    result = read_object(filename)
    paths = [result[name] for name in ("runtime", "host", "installed_runtime", "voice")]
    if any(
        not isinstance(value, str)
        or not Path(value).is_absolute()
        or "\n" in value
        or "\r" in value
        for value in paths
    ):
        raise ValueError("Invalid prepared launch paths")
    print("\n".join(paths))


if __name__ == "__main__":
    try:
        if sys.argv[1] == "--launch-paths":
            launch_paths(Path(sys.argv[2]))
        else:
            print(json.dumps(check(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])))
    except (*FAILURES, sqlite3.Error) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
