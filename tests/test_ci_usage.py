"""Usage failures must stay unknown, never become free capacity."""

import base64
import json
import subprocess
import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ci_usage import command_json, report


class UsageReport(unittest.TestCase):
    def test_api_failure_does_not_report_zero_or_run_blacksmith(self):
        with patch("ci_usage.command_json", side_effect=RuntimeError("private detail")) as command:
            result = report(datetime(2026, 10, 25, tzinfo=UTC))
        self.assertEqual(result["github_billing"]["status"], "unavailable")
        self.assertEqual(result["project_blacksmith_reservations"]["status"], "unavailable")
        self.assertEqual(result["blacksmith_usage"]["status"], "not_requested")
        self.assertNotIn("private detail", json.dumps(result))
        self.assertTrue(all(call.args[0][0] == "gh" for call in command.call_args_list))

    def test_reports_provider_data_separately_from_local_reservations(self):
        now = datetime(2026, 10, 25, tzinfo=UTC)
        state = {
            "schema": 1,
            "blacksmith_reservations": {
                "1": {"at": now.isoformat(), "minutes": 960, "runtime_key": "a"}
            },
        }
        billing = {"usageItems": [{"grossQuantity": 1128, "netQuantity": 0}]}
        with patch(
            "ci_usage.command_json",
            side_effect=[
                billing,
                {"active_caches_size_in_bytes": 123},
                {"content": base64.b64encode(json.dumps(state).encode()).decode()},
                {"provider": "unmodified data"},
            ],
        ) as command:
            result = report(now, blacksmith=True)
        self.assertEqual(result["github_billing"]["data"], billing)
        allowance = result["project_blacksmith_reservations"]["data"]
        self.assertEqual(allowance["reserved_minutes"], 960)
        self.assertFalse(allowance["provider_balance_verified"])
        self.assertIn("year=2026&month=10", command.call_args_list[0].args[0][-1])
        self.assertEqual(command.call_args_list[-1].args[0][0:2], ["blacksmith", "usage"])
        self.assertEqual(result["blacksmith_usage"]["data"], {"provider": "unmodified data"})

    def test_command_errors_do_not_expose_stderr(self):
        with patch(
            "ci_usage.subprocess.run",
            return_value=subprocess.CompletedProcess(["gh"], 1, "", "private token"),
        ):
            with self.assertRaisesRegex(RuntimeError, "gh request failed") as error:
                command_json(["gh", "api"])
        self.assertNotIn("private token", str(error.exception))
