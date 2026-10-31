"""Commit a reviewed file plan, publish a candidate, then approve that exact build.

Run only after local testing. This script is deliberately never run by the agent.
It waits for successful qualification and approval workflows, reusing the approved
runtime. State is retained so interruption cannot silently publish a second build.
"""

import argparse
import json
import subprocess
import time
from pathlib import Path

from common import ROOT, read_json, sha256, write_json

REPO = "discord-patched-electron/discord-patched-electron"


def command(*args):
    return subprocess.check_output([str(arg) for arg in args], cwd=ROOT, text=True).strip()


def api(endpoint):
    return json.loads(command("gh", "api", f"repos/{REPO}/{endpoint}"))


def changed_paths():
    # Disable rename detection so every deletion and addition is explicitly planned.
    result = subprocess.check_output(
        [
            "git",
            "-c",
            "status.renames=false",
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        ],
        cwd=ROOT,
    )
    return {entry[3:].decode() for entry in result.split(b"\0") if entry}


def validate_plan(plan):
    paths = [path for group in plan["commits"] for path in group["files"]]
    if len(paths) != len(set(paths)) or set(paths) != changed_paths():
        raise ValueError("Working tree paths differ from the reviewed commit plan")
    for group in plan["commits"]:
        if not group["message"].strip() or not group["files"]:
            raise ValueError("Empty commit group")
        for name, expected in group["files"].items():
            path = ROOT / name
            if Path(name).is_absolute() or ".." in Path(name).parts or path.is_symlink():
                raise ValueError("Unsafe commit path")
            actual = sha256(path) if path.is_file() else None
            if actual != expected:
                raise ValueError(f"File changed after review: {name}")


def wait_run(run_id, deadline):
    while time.monotonic() < deadline:
        run = api(f"actions/runs/{run_id}")
        if run["status"] == "completed":
            if run["conclusion"] != "success":
                raise RuntimeError(f"Workflow failed: {run['html_url']}")
            return run
        print(f"Waiting: {run['html_url']} ({run['status']})", flush=True)
        time.sleep(20)
    raise TimeoutError("Workflow wait expired; rerun with the same state to resume")


def dispatch(workflow, fields, state, state_path, key, deadline):
    # Save the pre-dispatch set first. If interrupted after dispatch, rediscover
    # only a unique new run on the recorded SHA; never dispatch a duplicate.
    workflow_path = f"actions/workflows/{workflow}/runs?event=workflow_dispatch&per_page=100"
    if key not in state:
        head = api("git/ref/heads/main")["object"]["sha"]
        state[key] = {
            "head": head,
            "before": [run["id"] for run in api(workflow_path)["workflow_runs"]],
        }
        write_json(state_path, state)
        args = ["gh", "workflow", "run", workflow, "--repo", REPO, "--ref", "main"]
        for name, value in fields.items():
            args += ["-f", f"{name}={value}"]
        command(*args)
    request = state[key]
    while "id" not in request and time.monotonic() < deadline:
        runs = [
            run
            for run in api(workflow_path)["workflow_runs"]
            if run["id"] not in request["before"] and run["head_sha"] == request["head"]
        ]
        if len(runs) > 1:
            raise RuntimeError("Multiple matching workflow runs; refusing ambiguous approval")
        if runs:
            request["id"] = runs[0]["id"]
            write_json(state_path, state)
        else:
            time.sleep(5)
    if "id" not in request:
        raise TimeoutError("Dispatch not found; inspect GitHub Actions before retrying")
    return wait_run(request["id"], deadline)


def candidate_for_run(run_id, head):
    # The manifest's workflow and source SHA must both match; 'latest' is unsafe.
    from releases import manifest_tag, published_manifest, published_releases

    matches = []
    for release in published_releases():
        # Avoid downloading unrelated historic assets: release target is our SHA.
        if release.get("target_commitish") != head:
            continue
        manifest = published_manifest(release)
        if str(manifest.get("workflow_run")) == str(run_id) and manifest["source_commit"] == head:
            if manifest.get("source_dirty") or not manifest.get("signing_required"):
                raise ValueError("Candidate must be clean and signed")
            matches.append(manifest_tag(manifest))
    if len(matches) != 1:
        raise ValueError("Expected exactly one signed candidate from this successful run")
    return matches[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--state", type=Path, default=ROOT / "out/working-tree-publication.json")
    parser.add_argument("--timeout-hours", type=float, default=6)
    args = parser.parse_args()
    if command("git", "branch", "--show-current") != "main":
        parser.error("Run on main")
    state = read_json(args.state) if args.state.exists() else {}
    plan_hash = sha256(args.plan)
    if state and state.get("plan_sha256") != plan_hash:
        parser.error("State belongs to another plan; choose a new --state file")
    deadline = time.monotonic() + args.timeout_hours * 3600
    if not state:
        plan = read_json(args.plan)
        if command("git", "diff", "--cached", "--name-only"):
            parser.error("Index must be empty before applying the commit plan")
        if command("git", "rev-parse", "HEAD") != plan["base"]:
            parser.error("HEAD differs from the reviewed plan")
        validate_plan(plan)
        command("git", "fetch", "origin", "main")
        command("git", "merge-base", "--is-ancestor", "origin/main", "HEAD")
        subprocess.run(["bun", "run", "lint"], cwd=ROOT, check=True)
        subprocess.run(["bun", "run", "test"], cwd=ROOT, check=True)
        validate_plan(plan)
        state = {"plan_sha256": plan_hash, "commits": [], "committing": True}
        write_json(args.state, state)
        for group in plan["commits"]:
            command("git", "add", "--", *group["files"])
            command("git", "commit", "-m", group["message"])
            state["commits"].append(command("git", "rev-parse", "HEAD"))
            write_json(args.state, state)
        state["head"] = command("git", "rev-parse", "HEAD")
        state["committing"] = False
        write_json(args.state, state)
    if state.get("committing"):
        parser.error(
            "Interrupted while committing. Review the index/commits before creating a new plan"
        )
    if changed_paths() or command("git", "rev-parse", "HEAD") != state["head"]:
        parser.error("Working tree or HEAD changed since this publication began")
    if not state.get("pushed"):
        command("git", "push", "origin", "main")
        state["pushed"] = True
        write_json(args.state, state)
    if "release" not in state and api("git/ref/heads/main")["object"]["sha"] != state["head"]:
        parser.error("Remote main moved; refusing to build different code")
    build = dispatch(
        "release.yml",
        {"publish": "true", "discord_channel": "canary"},
        state,
        args.state,
        "release",
        deadline,
    )
    if build["head_sha"] != state["head"]:
        parser.error("Release workflow source differs from committed code")
    tag = candidate_for_run(build["id"], state["head"])
    state["tag"] = tag
    write_json(args.state, state)
    approval = dispatch(
        "approve-release.yml",
        {"release_tag": tag, "tested": "true"},
        state,
        args.state,
        "approval",
        deadline,
    )
    release = api("releases/tags/" + tag)
    if release["draft"] or release["prerelease"]:
        parser.error("Approval completed but release is not stable")
    state["approved"] = True
    write_json(args.state, state)
    print(f"Stable release: {release['html_url']}\nApproval: {approval['html_url']}")


if __name__ == "__main__":
    main()
