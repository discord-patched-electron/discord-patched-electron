"""Read authenticated provider usage; never infer billing from GitHub wall time."""

import json
import math
import os
import subprocess
from datetime import UTC, datetime

from common import ROOT, read_json, write_json

ORGANIZATION = "discord-patched-electron"
RUNNER = "blacksmith-16vcpu-ubuntu-2404"
FREE_MINUTES = 3000  # Published monthly x64 2-vCPU allowance per organization.


def cli(*args):
    result = subprocess.run(
        [
            os.environ.get("BLACKSMITH_CLI", "blacksmith"),
            *args,
            "--org",
            ORGANIZATION,
            "--format",
            "json",
        ],
        env={**os.environ, "BLACKSMITH_DISABLE_AUTO_UPDATE": "1"},
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return json.loads(result.stdout)


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("Invalid Blacksmith usage number")
    return value


def snapshot(now):
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    jobs, cursor = [], ""
    # Read completed jobs first, then aggregates. Missing/unreported jobs retain
    # their full reservation, including cancelled, deleted and failed runs.
    for _ in range(100):
        page = cli(
            "jobs",
            "list",
            "--since",
            "30d",
            "--limit",
            "200",
            *(["--cursor", cursor] if cursor else []),
        )
        if page["installation"]["installation_name"] != ORGANIZATION:
            raise ValueError("Wrong Blacksmith organization")
        jobs.extend(page["jobs"])
        next_cursor = page["next_cursor"]
        if not next_cursor:
            break
        if next_cursor == cursor:
            raise ValueError("Blacksmith pagination did not advance")
        cursor = next_cursor
    else:
        raise ValueError("Incomplete Blacksmith job listing")
    report = cli("usage", "--start-time", start.isoformat(), "--end-time", now.isoformat())
    if report["installation"]["installation_name"] != ORGANIZATION:
        raise ValueError("Wrong Blacksmith organization")
    if (
        datetime.fromisoformat(report["window"]["start"].replace("Z", "+00:00")) != start
        or datetime.fromisoformat(report["window"]["end"].replace("Z", "+00:00")) != now
    ):
        raise ValueError("Blacksmith returned the wrong billing window")
    # CLI billing_minutes are platform-weighted *vCPU* minutes; the free tier
    # uses *2-vCPU* minutes. Verified against the provider's runner documentation.
    used = math.ceil(number(report["summary"]["billing_minutes"]) / 2)
    catalog = cli("runners", "catalog")["skus"]
    runner = next(s for s in catalog if s["label"] == RUNNER)
    if runner["vcpus"] != 16 or runner["cost_per_minute_usd"] != 0.032:
        raise ValueError("Blacksmith runner pricing changed; review the quota conversion")
    settled = {}
    for job in jobs:
        if (
            job["repository_name"] != ORGANIZATION + "/" + ORGANIZATION
            or job["runner_label"] != RUNNER
        ):
            continue
        if job["conclusion"] not in (
            "success",
            "failure",
            "cancelled",
            "timed_out",
            "neutral",
            "skipped",
        ):
            continue
        at = datetime.fromisoformat(job["created_at"].replace("Z", "+00:00"))
        if at <= now:
            settled.setdefault(str(job["run_id"]), 0)
            settled[str(job["run_id"])] += (
                math.ceil(number(job["billable_minutes"]) / 2) if at >= start else 0
            )
    if sum(settled.values()) > used:
        raise ValueError("Provider aggregates have not caught up with completed jobs")
    return {
        "at": now.isoformat(),
        "period_start": start.isoformat(),
        "used_minutes": used,
        "limit_minutes": FREE_MINUTES,
        "settled_runs": settled,
        "runner_rate": 8,
    }


if __name__ == "__main__":
    state_path = ROOT / "out/state/state.json"
    state = read_json(state_path)
    try:
        state["blacksmith_usage"] = snapshot(datetime.now(UTC).replace(microsecond=0))
        state.pop("blacksmith_usage_error", None)
        for run_id in state["blacksmith_usage"]["settled_runs"]:
            if run_id in state.get("blacksmith_reservations", {}):
                state["blacksmith_reservations"][run_id]["provider_settled_at"] = state[
                    "blacksmith_usage"
                ]["at"]
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        StopIteration,
        subprocess.SubprocessError,
    ) as error:
        state.pop("blacksmith_usage", None)
        state["blacksmith_usage_error"] = type(error).__name__
        print("Blacksmith usage unavailable; hosted compilation will remain blocked.")
    write_json(state_path, state)
