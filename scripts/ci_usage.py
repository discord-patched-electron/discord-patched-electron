"""Read provider usage on demand without starting a runner or changing the budget."""

import argparse
import base64
import json
import subprocess
from datetime import UTC, datetime

from blacksmith_budget import budget_status
from state_branch import PROJECT


def command_json(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    if result.returncode:
        # Provider errors can contain account details. Do not echo raw stderr or tokens.
        raise RuntimeError(f"{command[0]} request failed (exit {result.returncode})")
    return json.loads(result.stdout)


def report(now: datetime, blacksmith: bool = False) -> dict:
    organization = PROJECT.split("/")[0]
    result = {"checked_at": now.isoformat(), "billing_is_not_a_realtime_balance": True}

    def collect(name, callback):
        try:
            result[name] = {"status": "available", "data": callback()}
        except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
            result[name] = {"status": "unavailable", "error": type(error).__name__}

    def api(endpoint):
        return command_json(["gh", "api", endpoint])

    collect(
        "github_billing",
        lambda: api(
            f"organizations/{organization}/settings/billing/usage/summary"
            f"?year={now.year}&month={now.month}"
        ),
    )
    collect("github_cache", lambda: api(f"repos/{PROJECT}/actions/cache/usage"))

    def reservations():
        contents = api(f"repos/{PROJECT}/contents/state.json?ref=ci-state")
        state = json.loads(base64.b64decode(contents["content"]))
        return budget_status(state, now)

    collect("project_blacksmith_reservations", reservations)
    if blacksmith:
        collect(
            "blacksmith_usage",
            lambda: command_json(
                [
                    "blacksmith",
                    "usage",
                    "--org",
                    organization,
                    "--since",
                    "30d",
                    "--breakdown-by",
                    "runner_type,repo",
                    "--format",
                    "json",
                ]
            ),
        )
    else:
        result["blacksmith_usage"] = {
            "status": "not_requested",
            "hint": "Authenticate the official Blacksmith CLI, then add --blacksmith. "
            "Reservations are not your provider balance.",
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--blacksmith", action="store_true", help="Also query the authenticated CLI"
    )
    args = parser.parse_args()
    print(json.dumps(report(datetime.now(UTC), args.blacksmith), indent=2))


if __name__ == "__main__":
    main()
