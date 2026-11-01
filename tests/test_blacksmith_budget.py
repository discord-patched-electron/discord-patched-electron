"""Provider usage plus unsettled reservations bounds every hosted allocation."""

import os
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from blacksmith_budget import RUNNER, budget_status, main, reserve
from state_branch import PROJECT


def state_at(now, used=0, settled=None):
    return {
        "schema": 1,
        "blacksmith_usage": {
            "at": now.isoformat(),
            "period_start": now.replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            ).isoformat(),
            "limit_minutes": 3000,
            "runner_rate": 8,
            "used_minutes": used,
            "settled_runs": settled or {},
        },
    }


class HostedBudget(unittest.TestCase):
    def test_full_allowance_and_partial_final_attempt_are_available(self):
        now = datetime(2026, 11, 1, tzinfo=UTC)
        state = state_at(now, 8)
        for run in ("1", "2", "3"):
            reserve(state, now, run, "key")
        status = budget_status(state, now)
        self.assertEqual(status["remaining_minutes"], 112)
        self.assertFalse(status["can_build"])  # Too little time to build and upload.
        with self.assertRaises(ValueError):
            reserve(state, now, "4", "key")
        partial = state_at(now, 2500)
        result = reserve(partial, now, "1", "key")
        self.assertEqual(result["job_minutes"], 62)
        self.assertEqual(result["build_minutes"], 47)
        self.assertEqual(partial["blacksmith_reservations"]["1"]["minutes"], 496)

    def test_only_provider_settles_failed_jobs_not_github_status(self):
        now = datetime(2026, 11, 1, tzinfo=UTC)
        state = state_at(now)
        reserve(state, now, "1", "key")
        state["blacksmith_reservations"]["1"].update(status="completed", conclusion="failure")
        self.assertEqual(budget_status(state, now)["reserved_minutes"], 960)
        state["blacksmith_usage"].update(used_minutes=8, settled_runs={"1": 8})
        result = budget_status(state, now)
        self.assertEqual(result["reserved_minutes"], 0)
        self.assertEqual(result["remaining_minutes"], 2992)
        with self.assertRaisesRegex(ValueError, "already reserved"):
            reserve(state, now, "1", "key")

    def test_missing_stale_future_or_previous_month_usage_fails_closed(self):
        now = datetime(2026, 11, 1, tzinfo=UTC)
        for state in (
            {"schema": 1},
            state_at(now - timedelta(minutes=6)),
            state_at(now + timedelta(seconds=1)),
            state_at(now - timedelta(seconds=1)),
        ):
            with self.subTest(state=state):
                self.assertFalse(budget_status(state, now)["can_build"])
                with self.assertRaises(ValueError):
                    reserve(state, now, "1", "key")

    def test_month_reset_does_not_refund_an_unreported_running_job(self):
        now = datetime(2026, 11, 1, tzinfo=UTC)
        state = state_at(now)
        state["blacksmith_reservations"] = {
            "1": {
                "at": (now - timedelta(minutes=10)).isoformat(),
                "minutes": 960,
                "runtime_key": "key",
            }
        }
        self.assertEqual(budget_status(state, now)["remaining_minutes"], 2040)
        state["blacksmith_reservations"]["1"]["provider_settled_at"] = now.isoformat()
        self.assertEqual(budget_status(state, now)["remaining_minutes"], 3000)

    def test_invalid_ledger_fails_closed(self):
        now = datetime.now(UTC)
        state = state_at(now)
        state["blacksmith_reservations"] = {"1": {"at": now.isoformat(), "minutes": -1}}
        for invalid in ({}, {"schema": 2}, state):
            with self.assertRaises(ValueError):
                reserve(invalid, now, "2", "key")

    def test_failed_state_push_never_authorizes_runner(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            files = {
                "channels.json": {
                    "compiler": {
                        "enabled": True,
                        "provider": "blacksmith",
                        "runner": RUNNER,
                        "paid_usage_allowed": False,
                    }
                },
                "sources.lock.json": {"targets": {"prerelease": {}}},
                "update-policy.json": {"last_good_runtime": {"runtime_key": "old"}},
                "state.json": state_at(datetime.now(UTC)),
            }
            with (
                patch.dict(
                    os.environ,
                    {
                        "GITHUB_REPOSITORY": PROJECT,
                        "GITHUB_REF": "refs/heads/main",
                        "GITHUB_RUN_ATTEMPT": "1",
                        "GITHUB_RUN_ID": "123",
                        "ELECTRON_BUILD_TARGET": "prerelease",
                        "EXPECTED_RUNTIME_KEY": "new",
                        "GITHUB_OUTPUT": str(root / "output"),
                        "GITHUB_STEP_SUMMARY": str(root / "summary"),
                    },
                ),
                patch("blacksmith_budget.ROOT", root),
                patch("blacksmith_budget.read_json", side_effect=lambda path: files[path.name]),
                patch("blacksmith_budget.build_config", return_value={}),
                patch("blacksmith_budget.runtime_key", return_value="new"),
                patch(
                    "blacksmith_budget.save", side_effect=subprocess.CalledProcessError(1, "push")
                ),
            ):
                with self.assertRaises(subprocess.CalledProcessError):
                    main()
            self.assertFalse((root / "output").exists())
