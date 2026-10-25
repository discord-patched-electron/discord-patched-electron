"""Prepare Electron and Discord updates without changing a running process."""

import json
import os
import sys
from pathlib import Path

from client import ensure_client
from runtime import ensure_runtime


def check(app: Path, client_root: Path, channel: str) -> dict:
    offline = os.environ.get("DPE_CLIENT_OFFLINE") == "1"
    executable = ensure_runtime(app, client_root.parent, offline=offline)
    os.environ["DPE_RUNTIME_EXECUTABLE"] = str(executable)
    host = ensure_client(client_root, channel, offline=offline)
    return {"runtime": str(executable), "host": str(host)}


if __name__ == "__main__":
    print(json.dumps(check(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])))
