"""Apply the existing patch queue to an eligible release before scheduling C++."""

import argparse
import base64
import json
import os
import re
import subprocess
import time

from assemble import assemble
from build_reference import build_config
from common import ROOT, electron_channel, read_json, require_enabled_electron, run, write_json
from releases import api
from runtime_fingerprints import runtime_key


def publish_source_lock(original, updated, version):
    """CAS only the source file, preserving unrelated concurrent main commits."""
    endpoint = "contents/config/sources.lock.json"
    for attempt in range(3):
        current = api(endpoint + "?ref=main")
        contents = json.loads(base64.b64decode(current["content"]))
        if contents == updated:
            return  # Includes an accepted write whose response was lost.
        if contents != original:
            raise ValueError("Source lock changed concurrently; rediscover before retrying")
        try:
            api(
                endpoint,
                method="PUT",
                data={
                    "message": f"Advance Electron to {version}",
                    "branch": "main",
                    "sha": current["sha"],
                    "content": base64.b64encode(
                        (json.dumps(updated, indent=2) + "\n").encode()
                    ).decode(),
                },
            )
            return
        except subprocess.CalledProcessError as error:
            # 409 can race with an unrelated branch update. Re-read the blob
            # before every retry; never merge or overwrite a changed source lock.
            if attempt == 2 or not re.search(r"HTTP (409|429|5\d\d)", error.stderr or ""):
                raise
            time.sleep(2**attempt)


def advance(state_commit, key):
    if not re.fullmatch("[a-f0-9]{40}", state_commit) or not re.fullmatch("[a-f0-9]{64}", key):
        raise ValueError("Expected immutable state and runtime identities")
    project = "discord-patched-electron/discord-patched-electron"
    if os.environ.get("GITHUB_REPOSITORY") != project:
        raise ValueError("Only project workflows can advance the source lock")
    state = json.loads(
        subprocess.check_output(
            [
                "gh",
                "api",
                "-H",
                "Accept: application/vnd.github.raw+json",
                f"repos/{project}/contents/state.json?ref={state_commit}",
            ],
            text=True,
        )
    )
    spec = state["electron_work"][key]["target"]
    channel = electron_channel(spec["version"])
    require_enabled_electron(channel)
    lock_path = ROOT / "config/sources.lock.json"
    lock = read_json(lock_path)
    original = json.loads(json.dumps(lock))
    lock["targets"][channel] = spec
    if runtime_key(spec, lock, build_config(channel)) != key:
        raise ValueError("Source/native inputs changed since reservation; rediscover")
    # The compiler owns a different retained checkout. Never reset its objects.
    write_json(lock_path, lock)
    if not assemble(
        channel,
        ROOT / "out/update-source.git",
        ROOT / "out/update-source",
        ROOT / "out/source-update.json",
    ):
        raise ValueError("Source port required; last working runtime remains active")
    publish_source_lock(original, lock, spec["version"])
    # workflow_dispatch is explicit: GITHUB_TOKEN pushes do not trigger push CI.
    run(
        "gh",
        "workflow",
        "run",
        "build.yml",
        "--repo",
        project,
        "--ref",
        "main",
        "-f",
        "target=" + channel,
        "-f",
        "runtime_key=" + key,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state_commit")
    parser.add_argument("runtime_key")
    args = parser.parse_args()
    advance(args.state_commit, args.runtime_key)
