import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import ROOT, read_json
from schedule_updates import client_result, schedule
from update_policy import in_range, plan, urgent


class UpdatePolicy(unittest.TestCase):
    def setUp(self):
        self.policy = read_json(ROOT / "config/update-policy.json")
        self.sources = read_json(ROOT / "config/sources.lock.json")
        self.state = {
            "clients": read_json(ROOT / "config/clients.lock.json")["clients"],
            "releases": {
                "new": {
                    "status": "available",
                    "version": "46.0.0-alpha.2",
                    "channel": "prerelease",
                    "commit": "a" * 40,
                    "runtime_key": "b" * 64,
                }
            },
            "runtimes": {},
        }

    def test_four_days_since_last_attempt_not_publication_and_clients_never_wait(self):
        result = plan(self.state, self.policy, self.sources, "2026-10-25T03:00:00Z")
        self.assertEqual(result["electron"]["action"], "deferred")
        self.assertEqual(len(result["clients"]), 1)
        self.assertEqual(
            plan(self.state, self.policy, self.sources, "2026-10-28T02:41:42Z")["electron"][
                "action"
            ],
            "prepare-source",
        )
        self.state["last_compiler_attempt_at"] = "2026-10-27T00:00:00Z"
        self.assertEqual(
            plan(self.state, self.policy, self.sources, "2026-10-28T03:00:00Z")["electron"][
                "action"
            ],
            "deferred",
        )

    def test_security_bypass_only_for_affected_current_and_fixed_candidate(self):
        advisory = {
            "ghsa_id": "GHSA-test",
            "severity": "critical",
            "vulnerabilities": [
                {
                    "package": {"ecosystem": "npm", "name": "electron"},
                    "patched_versions": "46.0.0-alpha.2",
                    "vulnerable_version_range": ">= 46.0.0-alpha.1, < 46.0.0-alpha.2",
                }
            ],
        }
        self.state["security_advisories"] = [advisory]
        result = plan(self.state, self.policy, self.sources, "2026-10-24T03:00:00Z")
        self.assertEqual(result["electron"]["action"], "prepare-source")
        self.assertEqual(urgent("46.0.0-alpha.1", "46.0.0-alpha.1", [advisory], self.policy), [])
        self.assertEqual(urgent("46.0.0-alpha.3", "46.0.0-alpha.2", [advisory], self.policy), [])
        advisory["withdrawn_at"] = "2026-10-24T00:00:00Z"
        self.assertEqual(urgent("46.0.0-alpha.3", "46.0.0-alpha.1", [advisory], self.policy), [])
        with self.assertRaises(ValueError):
            in_range("46.0.0-alpha.1", "*")

    def test_same_inputs_and_failed_jobs_are_not_redispatched(self):
        first = schedule(self.state, self.policy, self.sources, "2026-10-28T03:00:00Z", "1", {})
        self.assertEqual(len(first["clients"]), 1)
        self.assertEqual(first["electron"]["action"], "prepare-source")
        again = schedule(
            self.state, self.policy, self.sources, "2026-10-28T04:00:00Z", "2", {"1": "failure"}
        )
        self.assertEqual(again["clients"], [])
        self.assertEqual(len(self.state["electron_work"]), 1)
        self.state["clients"]["canary"]["sources"]["discord_desktop_core"]["sha256"] = "c" * 64
        result = schedule(self.state, self.policy, self.sources, "2026-10-28T05:00:00Z", "3", {})
        self.assertEqual([i["channel"] for i in result["clients"]], ["canary"])
        self.assertEqual(result["electron"]["action"], "no-new-work")

    def test_running_compiler_not_cancelled_or_duplicated(self):
        result = schedule(
            self.state, self.policy, self.sources, "2026-10-28T03:00:00Z", "1", {}, True
        )
        self.assertEqual(result["electron"]["action"], "deferred")
        self.assertEqual(self.state["electron_work"], {})
        self.assertEqual(len(result["clients"]), 1)

    def test_successful_github_rerun_updates_failed_work_without_dispatch(self):
        schedule(self.state, self.policy, self.sources, "2026-10-25T03:00:00Z", "1", {})
        schedule(
            self.state, self.policy, self.sources, "2026-10-25T04:00:00Z", "2", {"1": "failure"}
        )
        self.assertTrue(all(w["status"] == "failure" for w in self.state["client_work"].values()))
        result = schedule(
            self.state, self.policy, self.sources, "2026-10-25T05:00:00Z", "3", {"1": "success"}
        )
        self.assertEqual(result["clients"], [])
        self.assertTrue(all(w["status"] == "success" for w in self.state["client_work"].values()))

    def test_one_channel_failure_does_not_fail_other_channels(self):
        jobs = [
            {
                "name": f"clients ({channel}) / {suffix}",
                "status": "completed",
                "conclusion": "failure"
                if channel == "canary" and suffix == "publish"
                else "success",
            }
            for channel in ("stable", "ptb", "canary")
            for suffix in (
                "inputs",
                "package",
                "publish",
                "native / validate",
                "bundled / validate",
                "audio / validate",
            )
        ]
        self.assertEqual(client_result("stable", jobs, True), "success")
        self.assertEqual(client_result("canary", jobs, True), "failure")
        self.assertIsNone(client_result("canary", jobs[:-1], False))
        self.assertEqual(client_result("canary", jobs[:-1], True), "failure")
        schedule(self.state, self.policy, self.sources, "2026-10-25T03:00:00Z", "1", {})
        schedule(
            self.state,
            self.policy,
            self.sources,
            "2026-10-25T04:00:00Z",
            "2",
            {"1": "failure", "1:stable": "success", "1:ptb": "success", "1:canary": "failure"},
        )
        self.assertEqual(
            {w["channel"]: w["status"] for w in self.state["client_work"].values()},
            {"canary": "failure"},
        )

    def test_bundled_binary_change_does_not_schedule_native_rebuild(self):
        self.state["releases"] = {}
        self.state["bundled_runtimes"] = {"canary": {"changed_since_previous_core": True}}
        result = plan(self.state, self.policy, self.sources, "2026-10-28T03:00:00Z")
        self.assertEqual(result["electron"]["action"], "unchanged")
        self.assertEqual(result["bundled_runtime_changes"], ["canary"])
