"""Apply the existing patch queue to an eligible release before scheduling C++."""

import argparse
import json
import os
import re
import subprocess

from assemble import assemble
from build_reference import build_config
from common import ROOT, electron_channel, read_json, require_enabled_electron, run, write_json
from runtime_fingerprints import runtime_key


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
    # A concurrent main change fails the ordinary fast-forward push. Never merge
    # generated locks or use force. Failed identities remain visible in ci-state.
    run("git", "add", "--", lock_path)
    changed = subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode
    if changed:
        run(
            "git",
            "-c",
            "user.name=github-actions[bot]",
            "-c",
            "user.email=41898282+github-actions[bot]@users.noreply.github.com",
            "commit",
            "-m",
            f"Advance Electron {channel} to {spec['version']}",
        )
        run(
            "git",
            "-c",
            "credential.helper=",
            "-c",
            "credential.helper=!gh auth git-credential",
            "push",
            "origin",
            "HEAD:refs/heads/main",
        )
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
