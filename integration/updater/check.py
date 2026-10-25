"""Prepare Electron and Discord updates without changing a running process."""

import importlib
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

from client import FAILURES, ensure_client
from installation import publish
from runtime import ensure_runtime
from support import progress


def prerequisites():
    for name in ("brotli",):
        if not shutil.which(name):
            raise ValueError(
                "Brotli is missing. Install it with your package manager; the updater will retry."
            )
    for name in ("capstone", "elftools"):
        try:
            importlib.import_module(name)
        except (ImportError, OSError) as error:
            raise ValueError(
                "This installation is missing its bundled update libraries. Extract or reinstall the complete package, then launch again."
            ) from error


def check(app: Path, client_root: Path, channel: str) -> dict:
    offline = os.environ.get("DPE_CLIENT_OFFLINE") == "1"
    progress("check", "Checking update requirements")
    if not offline:
        prerequisites()
    progress("check", "Checking patched Electron")
    executable = ensure_runtime(app, client_root.parent, offline=offline)
    progress("check", "Checking Discord modules")
    host = ensure_client(client_root, channel, offline=offline, executable=executable)
    if os.environ.get("DPE_PREPARE_ONLY") != "1":
        publish(app, client_root.parent, host, executable)
    progress("ready", "Starting Discord")
    return {"runtime": str(executable), "host": str(host)}


if __name__ == "__main__":
    try:
        print(json.dumps(check(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])))
    except (*FAILURES, sqlite3.Error) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
