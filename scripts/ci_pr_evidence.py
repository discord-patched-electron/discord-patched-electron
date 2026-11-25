"""Reuse integration checks from a same-repository PR on an identical merge tree."""

import argparse
import json
import os
import subprocess
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from common import write_json

WORKFLOW = ".github/workflows/check.yml"


def identity(scopes):
    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], text=True).strip()
    week = datetime.now(UTC).strftime("%G-%V")
    return {"schema": 1, "tree": tree, "week": week, "selected": scopes}


def api(repo, path):
    return json.loads(
        subprocess.check_output(["gh", "api", f"repos/{repo}/{path}"], text=True, timeout=30)
    )


def download(repo, run_id, attempt, directory):
    subprocess.run(
        [
            "gh",
            "run",
            "download",
            str(run_id),
            "--repo",
            repo,
            "--name",
            f"pr-source-{attempt}",
            "--dir",
            directory,
        ],
        check=True,
        timeout=30,
    )
    return json.loads((Path(directory) / "pr-source.json").read_text())


def matching_evidence(repo, commit, expected, wait_seconds=420):
    if not expected["selected"]:
        return None
    pulls = api(repo, f"commits/{commit}/pulls")
    pull = next(
        (
            pull
            for pull in pulls
            if pull.get("merge_commit_sha") == commit
            and pull.get("merged_at")
            and pull["base"]["ref"] == "main"
            and (pull["head"].get("repo") or {}).get("full_name") == repo
        ),
        None,
    )
    if pull is None:
        return None

    head = pull["head"]["sha"]
    runs = api(
        repo, f"actions/workflows/check.yml/runs?head_sha={head}&event=pull_request&per_page=10"
    )
    fresh = datetime.now(UTC) - timedelta(days=1)
    for run in runs["workflow_runs"]:
        if (
            run["event"] != "pull_request"
            or run["path"] != WORKFLOW
            or run["head_sha"] != head
            or run["head_repository"]["full_name"] != repo
            or run["repository"]["full_name"] != repo
            or datetime.fromisoformat(run["created_at"]) < fresh
            or (run["status"] == "completed" and run["conclusion"] != "success")
        ):
            continue
        run_id, attempt = int(run["id"]), int(run["run_attempt"])
        try:
            with tempfile.TemporaryDirectory(prefix="dpe-pr-proof-") as directory:
                proof = download(repo, run_id, attempt, directory)
        except (subprocess.CalledProcessError, OSError, ValueError):
            continue
        if (
            proof.get("tree") != expected["tree"]
            or proof.get("schema") != expected["schema"]
            or proof.get("week") != expected["week"]
            or proof.get("pull") != pull["number"]
            or not set(expected["selected"]) <= set(proof.get("selected", []))
        ):
            continue

        deadline = time.monotonic() + wait_seconds
        current = run
        while True:
            if current["run_attempt"] != attempt:
                return None
            if current["status"] == "completed":
                if current["conclusion"] != "success":
                    return None
                jobs = api(repo, f"actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100")
                gate = next((job for job in jobs["jobs"] if job["name"] == "Checks complete"), None)
                if gate and gate["conclusion"] == "success":
                    return run_id
                return None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            time.sleep(min(15, remaining))
            current = api(repo, f"actions/runs/{run_id}")
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("record", "reuse"))
    args = parser.parse_args()
    selected = json.loads(os.environ["PR_INTEGRATIONS"])
    if args.mode == "record":
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        write_json(
            Path("out/pr-source.json"),
            {**identity(selected), "pull": event["pull_request"]["number"]},
        )
        return

    repo, commit = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_SHA"]
    reused = None
    try:
        reused = matching_evidence(repo, commit, identity(selected))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f"PR evidence unavailable ({error}); running selected checks.")
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"reused={str(reused is not None).lower()}\n")
    if reused is not None:
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write(
                f"Reused {', '.join(selected)} from same-tree [PR Checks](https://github.com/{repo}/actions/runs/{reused}).\n"
            )


if __name__ == "__main__":
    main()
