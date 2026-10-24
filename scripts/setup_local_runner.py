"""Install this repository's containerized runner on the local Docker host.

Requires authenticated gh and Docker access. Registration credentials travel
through stdin only and are never written into this checkout or Docker config.
"""

import json
import os
import subprocess
from pathlib import Path

from common import ROOT, run

REPOSITORY = "discord-patched-electron/discord-patched-electron"
CONTAINER = "discord-electron-runner"
WORKSPACE = Path("/mnt/storage/discord-patched-electron-ci")


def main():
    if os.getuid() != 1000:
        raise ValueError("The workspace/image UID must be configured for this host first")
    existing = subprocess.run(["docker", "inspect", CONTAINER], capture_output=True)
    if existing.returncode == 0:
        raise ValueError(
            f"{CONTAINER} already exists; use docker start/stop, do not register twice"
        )
    WORKSPACE.mkdir(mode=0o700, exist_ok=True)
    for name in ("tmp", "tool-cache", "build-cache", "actions"):
        (WORKSPACE / name).mkdir(exist_ok=True)
    run("docker", "build", "--tag", "discord-electron-runner:local", ROOT / "ci/runner")
    run(
        "docker",
        "run",
        "--detach",
        "--init",
        "--restart",
        "unless-stopped",
        "--name",
        CONTAINER,
        "--cpus",
        "4",
        "--memory",
        "12g",
        "--memory-swap",
        "20g",
        "--pids-limit",
        "4096",
        "--mount",
        f"type=bind,src={WORKSPACE},dst=/work",
        "--env",
        "TMPDIR=/work/tmp",
        "--env",
        "RUNNER_TOOL_CACHE=/work/tool-cache",
        "discord-electron-runner:local",
    )
    token = json.loads(
        subprocess.check_output(
            [
                "gh",
                "api",
                "--method",
                "POST",
                f"repos/{REPOSITORY}/actions/runners/registration-token",
            ],
            text=True,
        )
    )["token"]
    # No shell interpolation of the credential and no token in docker's saved Env.
    script = (
        "set -eu; IFS= read -r registration_token; "
        './config.sh --unattended --url "$1" --token "$registration_token" '
        "--name he3als-pc-container --labels discord-electron-compiler "
        "--work /work/actions; touch .configured"
    )
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            CONTAINER,
            "bash",
            "-c",
            script,
            "register",
            f"https://github.com/{REPOSITORY}",
        ],
        input=token + "\n",
        text=True,
        capture_output=True,
    )
    print((result.stdout + result.stderr).replace(token, "[REDACTED]"))
    if result.returncode:
        run("docker", "stop", CONTAINER)
        raise RuntimeError("Runner registration failed; stopped the container for inspection")
    print(f"Runner registered. Build workspace: {WORKSPACE}")


if __name__ == "__main__":
    main()
