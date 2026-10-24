"""Reserve content-keyed work before reusable jobs start; no duplicate dispatches."""

import argparse
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from common import ROOT, read_json, write_json
from fingerprints import integration_key
from update_policy import plan

PROJECT = "discord-patched-electron/discord-patched-electron"


def client_result(channel, jobs, completed):
    required = {
        f"clients ({channel}) / {suffix}"
        for suffix in (
            "inputs",
            "package",
            "publish",
            "native / validate",
            "bundled / validate",
            "audio / validate",
        )
    }
    by_name = {job["name"]: job for job in jobs}
    # GitHub reports a skipped reusable call as "native", but an executed
    # call as "native / validate". Both describe the same required gate.
    found = {
        name: job
        for name in required
        if (job := by_name.get(name) or by_name.get(name.removesuffix(" / validate")))
    }
    if set(found) != required:
        return "failure" if completed else None
    if any(job["status"] != "completed" for job in found.values()):
        return None
    conclusions = {name.rsplit(" / ", 1)[-1]: job["conclusion"] for name, job in found.items()}
    if conclusions.get("inputs") == "success" and all(
        job["conclusion"] == "skipped"
        for name, job in found.items()
        if not name.endswith(" / inputs")
    ):
        return "success"  # This exact public candidate already exists.
    return "success" if all(job["conclusion"] == "success" for job in found.values()) else "failure"


def schedule(state, policy, sources, now, run_id, results, compiler_busy=False):
    work = state.setdefault("client_work", {})
    for item in [*work.values(), *state.setdefault("electron_work", {}).values()]:
        result_key = item["run"] + ":" + item["channel"] if "channel" in item else item["run"]
        if result_key not in results:
            result_key = item["run"]
        if result_key in results:
            # A GitHub re-run keeps its run ID. Refresh failed current work as
            # well as queued work, so a successful retry clears the failure.
            item["status"] = results[result_key] or "queued"
    decision = plan(state, policy, sources, now)
    for item in decision["clients"]:
        work[item["key"]] = {**item, "status": "queued", "run": run_id, "requested_at": now}
    native = decision["electron"]
    if (
        native["action"] == "prepare-source"
        and not compiler_busy
        and native["runtime_key"] not in state["electron_work"]
    ):
        state["electron_work"][native["runtime_key"]] = {
            "status": "queued",
            "run": run_id,
            "target": native["target"],
            "requested_at": now,
        }
        state["last_compiler_attempt_at"] = now
    else:
        native["action"] = "deferred" if compiler_busy else "no-new-work"
    return decision


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("GITHUB_REPOSITORY") != PROJECT:
        raise ValueError("Scheduling is limited to the project workflow")

    def api(endpoint):
        return json.loads(
            subprocess.check_output(
                ["gh", "api", f"repos/{PROJECT}/{endpoint}"], text=True, timeout=60
            )
        )

    state = read_json(args.state)
    policy = read_json(ROOT / "config/update-policy.json")
    run_id = os.environ["GITHUB_RUN_ID"]
    results = {}
    active = {
        integration_key(policy["last_good_runtime"]["runtime_key"], client, "prerelease")
        for client in state["clients"].values()
    }
    clients = [
        item
        for key, item in state.get("client_work", {}).items()
        if key in active or item["status"] == "queued"
    ]
    native = sorted(state.get("electron_work", {}).values(), key=lambda item: item["requested_at"])
    watched = clients + [item for item in native if item["status"] == "queued"] + native[-1:]
    for item in watched:
        if item["status"] != "success" and item["run"] != run_id and item["run"] not in results:
            result = api("actions/runs/" + item["run"])
            results[item["run"]] = result["conclusion"] if result["status"] == "completed" else None
            jobs = api("actions/runs/" + item["run"] + "/jobs?filter=latest&per_page=100")["jobs"]
            for channel in state["clients"]:
                results[item["run"] + ":" + channel] = client_result(
                    channel, jobs, result["status"] == "completed"
                )
    runs = api("actions/workflows/build.yml/runs?per_page=20")["workflow_runs"]
    runs += api("actions/workflows/capacity.yml/runs?per_page=20")["workflow_runs"]
    busy = (
        any(r["status"] != "completed" for r in runs)
        or not read_json(ROOT / "config/channels.json")["compiler"]["enabled"]
    )
    now = datetime.now(UTC).isoformat()
    result = schedule(
        state, policy, read_json(ROOT / "config/sources.lock.json"), now, run_id, results, busy
    )
    write_json(args.state, state)
    write_json(ROOT / "out/discovery/update-plan.json", result)
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write("clients=" + json.dumps([x["channel"] for x in result["clients"]]) + "\n")
        output.write(
            "electron="
            + (
                result["electron"].get("runtime_key", "")
                if result["electron"]["action"] == "prepare-source"
                else ""
            )
            + "\n"
        )
        output.write(
            "reference_run="
            + read_json(ROOT / "config/update-policy.json")["last_good_runtime"]["run"]
            + "\n"
        )
