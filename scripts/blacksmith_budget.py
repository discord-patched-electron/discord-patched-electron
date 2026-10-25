"""Reserve a bounded hosted build before allocating a Blacksmith runner."""

import os
from datetime import UTC, datetime, timedelta

from build_reference import build_config
from common import ROOT, read_json, require_enabled_electron, write_json
from releases import api
from runtime_fingerprints import runtime_key
from state_branch import PROJECT, save

RUNNER = "blacksmith-16vcpu-ubuntu-2404"
JOB_MINUTES = 120
BUILD_MINUTES = 105
RATE = 8  # Blacksmith bills in 2-vCPU-equivalent minutes.
BUDGET = 2400  # Leave 600 of the 3,000 free minutes outside this workflow.
WINDOW = timedelta(days=31)


def budget_status(state: dict, now: datetime) -> dict:
    if state.get("schema") != 1:
        raise ValueError("Missing or unsupported durable state")
    recent = [
        record
        for record in state.get("blacksmith_reservations", {}).values()
        if datetime.fromisoformat(record["at"]) + WINDOW > now
    ]
    if any(type(record["minutes"]) is not int or record["minutes"] <= 0 for record in recent):
        raise ValueError("Invalid budget reservation")
    used = sum(record["minutes"] for record in recent)
    return {
        "reserved_minutes": used,
        "limit_minutes": BUDGET,
        "next_build_minutes": JOB_MINUTES * RATE,
        "can_build": used + JOB_MINUTES * RATE <= BUDGET,
        "window_days": WINDOW.days,
        "provider_balance_verified": False,
    }


def reserve(state: dict, now: datetime, run_id: str, key: str):
    status = budget_status(state, now)
    records = state.setdefault("blacksmith_reservations", {})
    if run_id in records:
        raise ValueError("This run already reserved a runner. Dispatch a new run to retry.")
    charge = status["next_build_minutes"]
    if not status["can_build"]:
        raise ValueError(
            f"Blacksmith budget reserved: {status['reserved_minutes']}/{BUDGET} minutes in 31 days. "
            f"This build needs {charge}. No runner was allocated. "
            "Retry after older reservations expire. Client packaging can continue."
        )
    # Never refund from job history. Failed/cancelled/deleted runs still count,
    # even when they used less than their reserved maximum.
    records[run_id] = {"at": now.isoformat(), "minutes": charge, "runtime_key": key}


def main():
    if (
        os.environ.get("GITHUB_REPOSITORY") != PROJECT
        or os.environ.get("GITHUB_REF") != "refs/heads/main"
    ):
        raise ValueError("Hosted builds are restricted to the project main branch")
    if os.environ.get("GITHUB_RUN_ATTEMPT") != "1":
        raise ValueError("Dispatch a new build instead of re-running paid jobs")
    compiler = read_json(ROOT / "config/channels.json")["compiler"]
    if (
        not compiler["enabled"]
        or compiler["provider"] != "blacksmith"
        or compiler["runner"] != RUNNER
        or compiler["paid_usage_allowed"]
    ):
        raise ValueError("Hosted compilation is disabled or has an unsupported runner policy")
    target = os.environ["ELECTRON_BUILD_TARGET"]
    require_enabled_electron(target)
    sources = read_json(ROOT / "config/sources.lock.json")
    key = runtime_key(sources["targets"][target], sources, build_config(target))
    if os.environ.get("EXPECTED_RUNTIME_KEY") not in (None, "", key):
        raise ValueError("Source changed since scheduling")
    good = read_json(ROOT / "config/update-policy.json")["last_good_runtime"]
    if good["runtime_key"] == key:
        raise ValueError(f"Reuse completed compiler run {good['run']} for this runtime")
    directory = ROOT / "out/state"
    state = read_json(directory / "state.json")
    for run_id, record in state.get("blacksmith_reservations", {}).items():
        if record["runtime_key"] == key:
            result = api("actions/runs/" + run_id)
            if result["status"] != "completed" or result["conclusion"] == "success":
                raise ValueError(f"Runtime already has active or successful compiler run {run_id}")
    reserve(state, datetime.now(UTC), os.environ["GITHUB_RUN_ID"], key)
    write_json(directory / "state.json", state)
    save(directory)  # A concurrent state write fails closed before allocation.
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(
            f"runner={RUNNER}\ntimeout={JOB_MINUTES}\nbuild_minutes={BUILD_MINUTES}\n"
            f"runtime_key={key}\n"
        )
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
        summary.write(
            f"Reserved at most {JOB_MINUTES * RATE} Blacksmith allowance minutes. "
            f"The job is capped at {JOB_MINUTES} wall minutes. "
            f"Project reservations are capped at {BUDGET} minutes per rolling 31 days.\n"
        )


if __name__ == "__main__":
    main()
