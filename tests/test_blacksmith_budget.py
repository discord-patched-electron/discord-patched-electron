"""Hosted retries cannot spend past the durable reservation budget."""

import os
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from blacksmith_budget import BUDGET, JOB_MINUTES, RATE, RUNNER, WINDOW, main, reserve
from state_branch import PROJECT


class HostedBudget(unittest.TestCase):
    def test_failed_state_push_never_authorizes_runner_allocation(self):
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
                "state.json": {"schema": 1},
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
                        "GITHUB_OUTPUT": str(root / "outputs"),
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
            self.assertFalse((root / "outputs").exists())
            self.assertFalse((root / "summary").exists())

    def test_reserves_full_timeout_and_blocks_next_build_without_mutating_ledger(self):
        state = {"schema": 1}
        now = datetime(2026, 10, 24, tzinfo=UTC)
        reserve(state, now, "1", "key")
        reserve(state, now, "2", "key")
        self.assertEqual(state["blacksmith_reservations"]["1"]["minutes"], JOB_MINUTES * RATE)
        self.assertLessEqual(2 * JOB_MINUTES * RATE, BUDGET)
        with self.assertRaisesRegex(ValueError, "No runner was allocated"):
            reserve(state, now, "3", "key")
        self.assertNotIn("3", state["blacksmith_reservations"])

    def test_rerun_cannot_reuse_reservation_even_after_window_expires(self):
        state = {"schema": 1}
        now = datetime(2026, 10, 24, tzinfo=UTC)
        reserve(state, now, "1", "key")
        with self.assertRaisesRegex(ValueError, "already reserved"):
            reserve(state, now + WINDOW, "1", "key")

    def test_rolling_window_does_not_reset_at_calendar_month_boundary(self):
        state = {"schema": 1}
        start = datetime(2026, 10, 31, tzinfo=UTC)
        for run_id in ("1", "2"):
            reserve(state, start, run_id, "key")
        with self.assertRaises(ValueError):
            reserve(state, start + timedelta(days=1), "3", "key")
        reserve(state, start + WINDOW, "3", "key")
        self.assertEqual(len(state["blacksmith_reservations"]), 3)

    def test_missing_or_invalid_ledger_fails_closed(self):
        now = datetime.now(UTC)
        for state in (
            {},
            {"schema": 2},
            {
                "schema": 1,
                "blacksmith_reservations": {
                    "1": {"at": now.isoformat(), "minutes": -100, "runtime_key": "key"}
                },
            },
        ):
            with self.subTest(state=state), self.assertRaises(ValueError):
                reserve(state, now, "2", "key")
