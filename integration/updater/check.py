"""Prepare Electron and Discord updates without changing a running process."""

import json
import os
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path

from application import prepare as prepare_application
from client import FAILURES, ensure_client
from installation import publish
from openh264 import ensure_requested as ensure_openh264
from runtime import ensure_runtime, reuse_approval_downloads
from support import progress, read_object


@reuse_approval_downloads()
def check(app: Path, client_root: Path, channel: str) -> dict:
    offline = os.environ.get("DPE_CLIENT_OFFLINE") == "1"
    quickstart = offline and os.environ.get("DPE_REQUIRE_CURRENT_CACHE") == "1"
    incoming = prepare_application(app, client_root.parent)
    if incoming:
        return {"application": str(incoming)}
    ensure_openh264(client_root.parent)
    progress("check", "Checking patched Electron")
    with ThreadPoolExecutor(max_workers=1) as helpers:
        runtime = helpers.submit(
            copy_context().run, ensure_runtime, app, client_root.parent, offline=offline
        )
        progress("check", "Checking Discord modules")
        host = ensure_client(
            client_root,
            channel,
            offline=offline,
            executable=runtime.result,
            require_current_cache=quickstart,
        )
        executable = runtime.result()
    result = {"runtime": str(executable), "host": str(host)}
    if os.environ.get("DPE_PREPARE_ONLY") != "1":
        progress("check", "Preparing Discord")
        installed = publish(app, client_root.parent, host, executable)
        state = read_object(host / "client.json")
        revision = state["modules"]["discord_voice"]["version"]
        result["installed_runtime"] = str(installed / "electron")
        result["voice"] = str(installed / "modules" / f"discord_voice-{revision}" / "discord_voice")
        if quickstart:
            result["defer_update"] = True
    progress("ready", "Starting Discord")
    return result


def launch_paths(filename: Path):
    """Read this launch's private result; never reselect background-update pointers."""
    result = read_object(filename)
    names = (
        ("application",)
        if "application" in result
        else ("runtime", "host", "installed_runtime", "voice")
    )
    paths = [result[name] for name in names]
    if any(
        not isinstance(value, str)
        or not Path(value).is_absolute()
        or "\n" in value
        or "\r" in value
        for value in paths
    ):
        raise ValueError("Invalid prepared launch paths")
    if names[0] == "runtime" and result.get("defer_update") is True:
        paths.append("1")
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
