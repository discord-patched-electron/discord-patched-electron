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
BUDGET = 3000  # The full published monthly allowance; provider usage is authoritative.
WINDOW = timedelta(days=31)


def budget_status(state: dict, now: datetime) -> dict:
    if state.get("schema") != 1:
        raise ValueError("Missing or unsupported durable state")
    usage = state.get("blacksmith_usage")
    base = {
        "reserved_minutes": 0,
        "limit_minutes": 3000,
        "next_build_minutes": JOB_MINUTES * RATE,
        "can_build": False,
        "provider_balance_verified": False,
        "window_days": 31,
    }
    if not usage:
        return {**base, "reason": "provider-usage-unavailable"}
    sampled = datetime.fromisoformat(usage["at"])
    month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if (
        not timedelta(0) <= now - sampled <= timedelta(minutes=5)
        or datetime.fromisoformat(usage["period_start"]) != month
    ):
        return {**base, "reason": "provider-usage-stale"}
    if usage["limit_minutes"] != 3000 or usage["runner_rate"] != RATE:
        raise ValueError("Unsupported provider quota or runner rate")
    records = state.get("blacksmith_reservations", {})
    if any(type(r["minutes"]) is not int or r["minutes"] <= 0 for r in records.values()):
        raise ValueError("Invalid budget reservation")
    pending = sum(
        r["minutes"]
        for run, r in records.items()
        if run not in usage["settled_runs"] and not r.get("provider_settled_at")
    )
    used = usage["used_minutes"]
    if type(used) is not int or used < 0:
        raise ValueError("Invalid provider usage")
    remaining = max(0, usage["limit_minutes"] - used - pending)
    timeout = min(JOB_MINUTES, remaining // RATE)
    # Keep the existing 15-minute artifact/cleanup window. Smaller balances can
    # fund shorter attempts instead of stranding every balance below 960 minutes.
    return {
        **base,
        "reserved_minutes": pending,
        "used_minutes": used,
        "remaining_minutes": remaining,
        "next_build_minutes": timeout * RATE,
        "job_minutes": timeout,
        "build_minutes": max(0, timeout - 15),
        "can_build": timeout > 15,
        "provider_balance_verified": True,
    }


def reserve(state: dict, now: datetime, run_id: str, key: str):
    status = budget_status(state, now)
    records = state.setdefault("blacksmith_reservations", {})
    if run_id in records:
        raise ValueError("This run already reserved a runner. Dispatch a new run to retry.")
    charge = status["next_build_minutes"]
    if not status["can_build"]:
        raise ValueError(
            f"Blacksmith allowance unavailable: {status.get('reason', 'insufficient balance')}. "
            f"This build needs {charge}. No runner was allocated. "
            "Retry after a fresh provider usage check or monthly reset. Client packaging can continue."
        )
    # Only completed jobs reported by Blacksmith can settle a reservation.
    # GitHub timings, missing runs and calendar changes cannot refund it.
    records[run_id] = {"at": now.isoformat(), "minutes": charge, "runtime_key": key}
    return status


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
            result = api("actions/runs/" + run_id, missing=True)
            if result is None:
                if not record.get("provider_settled_at"):
                    raise ValueError("Deleted compiler run has not been settled by Blacksmith")
                continue
            if result["status"] != "completed" or result["conclusion"] == "success":
                raise ValueError(f"Runtime already has active or successful compiler run {run_id}")
    status = reserve(state, datetime.now(UTC), os.environ["GITHUB_RUN_ID"], key)
    write_json(directory / "state.json", state)
    save(directory)  # A concurrent state write fails closed before allocation.
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(
            f"runner={RUNNER}\ntimeout={status['job_minutes']}\nbuild_minutes={status['build_minutes']}\n"
            f"runtime_key={key}\n"
        )
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
        summary.write(
            f"Provider reports {status['used_minutes']}/{BUDGET} monthly allowance minutes used. "
            f"Reserved at most {status['next_build_minutes']} for this job; "
            f"timeout {status['job_minutes']} wall minutes. "
            "Unreported/in-flight jobs retain their maximum reservations.\n"
        )


if __name__ == "__main__":
    main()
