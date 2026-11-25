"""Reuse completed source verification for the exact trusted main-branch commit."""

import argparse
import re
import subprocess
import tempfile
import time
from pathlib import Path

from common import ROOT, digest, read_json, write_json
from fetch_client_snapshot import fetch as fetch_clients
from releases import PROJECT, api

REQUIRED_STEPS = {
    "Verify source formatting and workflows",
    "Verify Rust formatting and lints",
    "Run unit suites",
}


def push_checks_run(commit: str) -> dict | None:
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise ValueError("Source verification requires an immutable commit")
    runs = api(
        f"actions/workflows/check.yml/runs?head_sha={commit}&branch=main&event=push&per_page=1"
    )["workflow_runs"]
    if not runs:
        return None
    run = runs[0]
    if (
        run["head_sha"] != commit
        or run["head_branch"] != "main"
        or run["head_repository"]["full_name"] != PROJECT
        or run["event"] != "push"
        or run["path"] != ".github/workflows/check.yml"
    ):
        raise ValueError("Untrusted source verification run")
    return run


def verification(commit: str, *, timeout: int = 1200) -> dict:
    run = push_checks_run(commit)
    if run is None:
        return {"schema": 1, "source_commit": commit, "mode": "fresh"}
    deadline = time.monotonic() + timeout
    while True:
        jobs = api(f"actions/runs/{run['id']}/jobs?filter=latest&per_page=100")["jobs"]
        job = next((j for j in jobs if j["name"] == "tests"), None)
        if job and job["status"] == "completed":
            if job["conclusion"] != "success":
                raise ValueError("Exact-commit source checks failed; repair them before release")
            steps = {step["name"]: step["conclusion"] for step in job["steps"]}
            if all(steps.get(name) == "success" for name in REQUIRED_STEPS):
                return {
                    "schema": 1,
                    "mode": "reused",
                    "source_commit": commit,
                    "checks_run": run["id"],
                    "job": job["id"],
                    "url": run["html_url"],
                }
            # Documentation-only or older workflow definitions have no complete
            # reusable unit proof. A standalone release must verify its source.
            return {"schema": 1, "source_commit": commit, "mode": "fresh"}
        current = api(f"actions/runs/{run['id']}")
        if current["status"] == "completed":
            raise ValueError("Source verification ended without a successful tests job")
        if time.monotonic() >= deadline:
            raise ValueError("Source checks are still pending; retry release after they finish")
        # Observe completion rather than concurrently duplicating the same suite.
        time.sleep(15)


def validate_bundled_report(report: dict, channel: str, candidate: dict):
    if (
        report.get("runtime_kind") != "official bundled baseline"
        or report.get("all_selected_passed") is not True
    ):
        raise ValueError("Native evidence is not a successful bundled-runtime check")
    matches = [item for item in report.get("combinations", []) if item.get("channel") == channel]
    if len(matches) != 1 or any(
        matches[0].get(key) is not True
        for key in ("passed", "native_fixture_passed", "offline_krisp_verified")
    ):
        raise ValueError("Native evidence does not qualify the requested channel")
    if matches[0].get("client_digest") != digest(candidate):
        raise ValueError("Native evidence has different client inputs")


def bundled_verification(commit: str, candidate: dict, channel: str, output: Path) -> dict:
    if candidate != read_json(ROOT / "config/clients.lock.json")["clients"].get(channel):
        return {"mode": "fresh", "reason": "Candidate differs from push-tested client lock"}
    run = push_checks_run(commit)
    if run is None:
        return {"mode": "fresh", "reason": "No matching push verification"}
    run_id = run["id"]
    deadline = time.monotonic() + 600
    while True:
        jobs = api(f"actions/runs/{run_id}/jobs?filter=latest&per_page=100")["jobs"]
        job = next((j for j in jobs if j["name"] == "native-baselines / validate"), None)
        if job and job["status"] == "completed":
            if job["conclusion"] == "skipped":
                return {"mode": "fresh", "reason": "Push did not select bundled native checks"}
            if job["conclusion"] != "success":
                raise ValueError("Push bundled native checks failed; repair before release")
            break
        run = api(f"actions/runs/{run_id}")
        if run["status"] == "completed" and job is None:
            return {"mode": "fresh", "reason": "Push has no bundled native job"}
        if time.monotonic() >= deadline:
            raise ValueError("Push bundled native checks are still pending")
        time.sleep(15)
    artifact = f"native-runtime-diagnostics-{run_id}-all-bundled"
    with tempfile.TemporaryDirectory(prefix="native-proof-") as temporary:
        subprocess.run(
            [
                "gh",
                "run",
                "download",
                str(run_id),
                "--repo",
                PROJECT,
                "--name",
                artifact,
                "--dir",
                temporary,
            ],
            check=True,
        )
        paths = [
            Path(temporary) / "native-runtime/runtime-checks.json",
            Path(temporary) / "runtime-checks.json",
        ]
        reports = [read_json(path) for path in paths if path.is_file()]
        if len(reports) != 1:
            raise ValueError("Bundled native evidence is missing or ambiguous")
        validate_bundled_report(reports[0], channel, candidate)
        write_json(output.parent / "native-runtime/runtime-checks.json", reports[0])
    return {
        "schema": 1,
        "mode": "reused",
        "source_commit": commit,
        "checks_run": run_id,
        "url": run["html_url"],
        "native_job": job["id"],
        "artifact": artifact,
        "client_digest": digest(candidate),
        "reused": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bundled-state")
    parser.add_argument("--discord-channel", default="canary")
    args = parser.parse_args()
    if args.bundled_state:
        clients = args.output.parent / "clients.lock.json"
        fetch_clients(args.bundled_state, clients)
        candidate = read_json(clients)["clients"][args.discord_channel]
        proof = bundled_verification(args.commit, candidate, args.discord_channel, args.output)
        write_json(args.output, proof)
        import os

        if target := os.environ.get("GITHUB_OUTPUT"):
            with Path(target).open("a") as stream:
                stream.write(f"reused={str(proof.get('reused', False)).lower()}\n")
        print(proof)
        return
    proof = verification(args.commit)
    if proof["mode"] == "fresh":
        subprocess.run(["bun", "run", "lint"], check=True)
        subprocess.run(["bun", "run", "test"], check=True)
    write_json(args.output, proof)
    print(proof)


if __name__ == "__main__":
    main()
