#!/usr/bin/env python3
"""DPE managed game launcher: stable command, current per-user application bundle."""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

MARKER = "DPE managed game launcher: stable command"


def state_path(environment):
    data = environment.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
    return Path(data) / "discord-patched-electron/game-capture.json"


def replace(path, data, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".dpe-game-capture-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fchmod(output.fileno(), mode)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def register(app, environment):
    app = Path(app).resolve(strict=True)
    launch = app / "capture/vkcapture/gamecapture-launch.py"
    if not launch.is_file():
        return False
    target = Path.home() / ".local/bin/dpe-game-capture"
    if target.is_symlink() or (target.exists() and MARKER not in target.read_text()[:512]):
        raise ValueError("Existing dpe-game-capture is not managed by DPE")
    replace(target, Path(__file__).read_bytes(), 0o755)
    replace(state_path(environment), json.dumps({"app": str(app)}).encode(), 0o600)
    return True


def launch(arguments, environment):
    command = arguments[1:] if arguments[:1] == ["--"] else arguments
    if not command:
        raise ValueError("Usage: dpe-game-capture -- game-command [arguments]")
    if command[0] in ("game-command", "%command%"):
        raise ValueError(
            "Use %command% in Steam launch options only. "
            "In a terminal, replace it with your game's executable and arguments."
        )
    app = environment.get("DPE_APP_ROOT")
    if not app:
        try:
            record = json.loads(state_path(environment).read_text())
            app = record.get("app") if isinstance(record, dict) else None
        except (OSError, ValueError):
            pass
    if isinstance(app, str) and Path(app).is_absolute():
        helper = Path(app) / "capture/vkcapture/gamecapture-launch.py"
        if helper.is_file():
            # Resolve the active bundle every invocation; the stable command has
            # no versioned path baked into it and preserves all game arguments.
            os.execvpe("python3", ["python3", "-I", str(helper), "--", *command], environment)
    upstream = shutil.which("obs-gamecapture", path=environment.get("PATH"))
    if upstream:
        os.execve(upstream, [upstream, *command], environment)
    raise ValueError("Open Discord Patched Electron once to register its game capture helper")


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--register":
        register(sys.argv[2], os.environ)
    else:
        launch(sys.argv[1:], dict(os.environ))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        print(f"DPE game capture: {error}", file=sys.stderr)
        raise SystemExit(2) from None
