"""Retrieve exact discovered client inputs by immutable project state commit."""

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

from channels import enabled_discord_channels
from client_inputs import validate_channel
from common import digest, write_json

PROJECT = "discord-patched-electron/discord-patched-electron"


def fetch(commit: str, output: Path):
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise ValueError("Client snapshot must name an immutable Git commit, not a moving branch")
    if os.environ.get("GH_REPO") != PROJECT:
        raise ValueError("Client snapshots are restricted to the project repository")
    state = json.loads(
        subprocess.check_output(
            [
                "gh",
                "api",
                "-H",
                "Accept: application/vnd.github.raw+json",
                f"repos/{PROJECT}/contents/state.json?ref={commit}",
            ],
            text=True,
            timeout=60,
        )
    )
    if state.get("schema") != 1 or not set(enabled_discord_channels()).issubset(
        state.get("clients", {})
    ):
        raise ValueError("Unsupported state/client snapshot")
    for channel, client in state["clients"].items():
        validate_channel(channel, client)
    if any(ch in state.get("client_errors", {}) for ch in enabled_discord_channels()):
        raise ValueError("Client snapshot contains a failed discovery; choose a healthy snapshot")
    snapshot = {
        "schema": 1,
        "clients": {ch: state["clients"][ch] for ch in enabled_discord_channels()},
    }
    write_json(output, snapshot)
    write_json(
        output.with_suffix(".provenance.json"),
        {
            "state_commit": commit,
            "clients_digest": digest(snapshot["clients"]),
            "client_errors": state.get("client_errors", {}),
            "publishable": False,
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("commit")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    fetch(args.commit, args.output)
