"""Use the provider CLI's observed schema and its actual minute units."""

import copy
import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from blacksmith_usage import ORGANIZATION, RUNNER, snapshot


class ProviderUsage(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 1, 7, tzinfo=UTC)
        installation = {"installation_name": ORGANIZATION}
        self.jobs = {
            "installation": installation,
            "jobs": [
                {
                    "run_id": "123",
                    "repository_name": ORGANIZATION + "/" + ORGANIZATION,
                    "runner_label": RUNNER,
                    "conclusion": "failure",
                    "created_at": "2026-11-01T06:47:25Z",
                    "billable_minutes": 16,
                }
            ],
            "next_cursor": "",
        }
        self.report = {
            "installation": installation,
            "window": {"start": "2026-11-01T00:00:00Z", "end": "2026-11-01T07:00:00Z"},
            "summary": {"billing_minutes": 16},
        }
        self.catalog = {"skus": [{"label": RUNNER, "vcpus": 16, "cost_per_minute_usd": 0.032}]}

    def test_16_vcpu_minutes_are_8_allowance_minutes(self):
        with patch(
            "blacksmith_usage.cli", side_effect=[self.jobs, self.report, self.catalog]
        ) as cli:
            result = snapshot(self.now)
        self.assertEqual(result["used_minutes"], 8)
        self.assertEqual(result["settled_runs"], {"123": 8})
        self.assertEqual(result["limit_minutes"], 3000)
        self.assertNotIn("--repo", cli.call_args_list[1].args)  # Organization-wide usage.

    def test_lagging_aggregates_malformed_usage_and_wrong_org_fail_closed(self):
        for mutate in (
            lambda r: r["summary"].update(billing_minutes=0),
            lambda r: r["summary"].update(billing_minutes=float("nan")),
            lambda r: r["summary"].update(billing_minutes=-1),
            lambda r: r["installation"].update(installation_name="other"),
            lambda r: r["window"].update(start="2026-10-01T00:00:00Z"),
        ):
            report = copy.deepcopy(self.report)
            mutate(report)
            with patch("blacksmith_usage.cli", side_effect=[self.jobs, report, self.catalog]):
                with self.assertRaises(ValueError):
                    snapshot(self.now)

    def test_changed_pricing_requires_review(self):
        self.catalog["skus"][0]["cost_per_minute_usd"] = 0.04
        with patch("blacksmith_usage.cli", side_effect=[self.jobs, self.report, self.catalog]):
            with self.assertRaisesRegex(ValueError, "pricing changed"):
                snapshot(self.now)

    def test_pagination_and_previous_month_settlement(self):
        first = copy.deepcopy(self.jobs)
        first["next_cursor"] = "next"
        first["jobs"][0].update(run_id="old", created_at="2026-10-31T00:00:00Z")
        with patch(
            "blacksmith_usage.cli", side_effect=[first, self.jobs, self.report, self.catalog]
        ) as cli:
            result = snapshot(self.now)
        self.assertEqual(result["settled_runs"], {"old": 0, "123": 8})
        self.assertIn("--cursor", cli.call_args_list[1].args)
