"""Reserve content-keyed work before reusable jobs start; no duplicate dispatches."""

import argparse
import json
import os
import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from blacksmith_budget import budget_status
from common import ROOT, electron_channel, read_json, write_json
from fingerprints import integration_key
from update_policy import date, plan

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


def electron_result(jobs, completed):
    job = next((job for job in jobs if job["name"] == "electron"), None)
    if not job:
        return "failure" if completed else None
    if job["status"] != "completed":
        return None
    return "success" if job["conclusion"] == "success" else "failure"


def schedule(
    state, policy, sources, now, run_id, results, compiler_busy=False, security_only=False
):
    work = state.setdefault("client_work", {})
    for item in [*work.values(), *state.setdefault("electron_work", {}).values()]:
        result_key = item["run"] + ":" + item.get("channel", "electron")
        if result_key not in results:
            result_key = item["run"]
        if result_key in results:
            # A GitHub re-run keeps its run ID. Refresh failed current work as
            # well as queued work, so a successful retry clears the failure.
            item["status"] = results[result_key] or "queued"
    decision = plan(state, policy, sources, now)
    if security_only:
        decision["clients"] = []
    for item in decision["clients"]:
        work[item["key"]] = {**item, "status": "queued", "run": run_id, "requested_at": now}
    native = decision["electron"]
    allowance = budget_status(state, datetime.fromisoformat(now.replace("Z", "+00:00")))
    decision["compiler_allowance"] = allowance
    if native["action"] == "prepare-source" and not allowance["can_build"]:
        native["action"] = "budget-deferred"
        return decision
    previous = state["electron_work"].get(native.get("runtime_key"))
    records = [
        r
        for r in state.get("blacksmith_reservations", {}).values()
        if r["runtime_key"] == native.get("runtime_key")
    ]
    request = state.get("compiler_requests", {}).get(native.get("runtime_key"))
    if request:
        records.append(request)
    if any(r.get("conclusion") == "success" for r in records):
        native["action"] = "compiled"
        return decision
    if any(r.get("status") != "completed" for r in records):
        native["action"] = "compiler-active"
        return decision
    if previous:
        attempts = previous.get("attempts", 1)
        if previous["status"] == "queued":
            native["action"] = "source-active"
            return decision
        new_urgency = set(native.get("urgent_reasons", [])) - set(
            previous.get("urgent_reasons", [])
        )
        if new_urgency:
            attempts = 0  # A newly published critical fix earns a fresh bounded attempt.
        if attempts >= policy.get("max_update_attempts", 3):
            native["action"] = "retry-exhausted"
            return decision
        # Success without a corresponding compiler reservation can be a lost
        # dispatch. Retry safely: the compiler allowance deduplicates the key.
        delay = policy.get("retry_minutes", [5, 15, 60])[min(attempts - 1, 2)]
        retry_at = date(previous["requested_at"]) + timedelta(minutes=delay)
        native["retry_at"] = retry_at.isoformat()
        if not new_urgency and date(now) < retry_at:
            native["action"] = "retry-deferred"
            return decision
    if native["action"] == "prepare-source" and (not compiler_busy or native.get("urgent_reasons")):
        state["electron_work"][native["runtime_key"]] = {
            "status": "queued",
            "run": run_id,
            "target": native["target"],
            "requested_at": now,
            "attempts": attempts + 1 if previous else 1,
            "urgent_reasons": native.get("urgent_reasons", []),
            "previous_runs": [*previous.get("previous_runs", []), previous["run"]]
            if previous
            else [],
        }
    elif native["action"] == "prepare-source":
        native["action"] = "compiler-busy"
    return decision


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--security-only", action="store_true")
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
        integration_key(
            policy["last_good_runtime"]["runtime_key"],
            client,
            electron_channel(policy["last_good_runtime"]["version"]),
        )
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
            results[item["run"] + ":electron"] = electron_result(
                jobs, result["status"] == "completed"
            )
            for channel in state["clients"]:
                results[item["run"] + ":" + channel] = client_result(
                    channel, jobs, result["status"] == "completed"
                )
    for compiler_run, reservation in state.get("blacksmith_reservations", {}).items():
        if reservation.get("status") != "completed":
            result = api("actions/runs/" + compiler_run)
            reservation.update(status=result["status"], conclusion=result["conclusion"])
    runs = api("actions/workflows/build.yml/runs?per_page=100")["workflow_runs"]
    for compiler in runs:
        match = re.fullmatch(r"Electron ([a-f0-9]{64})", compiler.get("display_title", ""))
        if match:
            key = match[1]
            request = state.setdefault("compiler_requests", {}).get(key)
            if request is None or int(compiler["id"]) > int(request["run"]):
                state["compiler_requests"][key] = {
                    "run": str(compiler["id"]),
                    "status": compiler["status"],
                    "conclusion": compiler["conclusion"],
                }
            elif str(compiler["id"]) == request["run"]:
                request.update(status=compiler["status"], conclusion=compiler["conclusion"])
    busy = (
        any(r["status"] != "completed" for r in runs)
        or not read_json(ROOT / "config/channels.json")["compiler"]["enabled"]
    )
    now = datetime.now(UTC).isoformat()
    result = schedule(
        state,
        policy,
        read_json(ROOT / "config/sources.lock.json"),
        now,
        run_id,
        results,
        busy,
        args.security_only,
    )
    write_json(args.state, state)
    write_json(ROOT / "out/discovery/update-plan.json", result)
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
        summary.write(
            "### Electron scheduling\n\n```json\n"
            + json.dumps(
                {"electron": result["electron"], "allowance": result["compiler_allowance"]},
                indent=2,
            )
            + "\n```\n"
        )
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
