"""Exact trusted source checks can be reused; pending or failed checks cannot."""

import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import source_verification as source
from common import read_json


class SourceVerification(unittest.TestCase):
    def setUp(self):
        self.commit = "a" * 40
        self.run = {
            "id": 12,
            "head_sha": self.commit,
            "head_branch": "main",
            "head_repository": {"full_name": source.PROJECT},
            "event": "push",
            "path": ".github/workflows/check.yml",
            "html_url": "https://github.com/project/actions/runs/12",
        }
        self.job = {
            "id": 34,
            "name": "tests",
            "status": "completed",
            "conclusion": "success",
            "steps": [{"name": n, "conclusion": "success"} for n in source.REQUIRED_STEPS],
        }

    def api(self, endpoint):
        if "/runs?" in endpoint:
            return {"workflow_runs": [self.run]}
        if "/jobs?" in endpoint:
            return {"jobs": [self.job]}
        return {"status": "in_progress"}

    def test_exact_completed_source_proof_is_reused(self):
        with patch.object(source, "api", side_effect=self.api):
            proof = source.verification(self.commit)
        self.assertEqual(proof["mode"], "reused")
        self.assertEqual(proof["source_commit"], self.commit)
        self.assertEqual((proof["checks_run"], proof["job"]), (12, 34))

    def test_wrong_source_repository_branch_event_or_workflow_cannot_authorize_reuse(self):
        original = copy.deepcopy(self.run)
        for key, value in (
            ("head_sha", "b" * 40),
            ("head_branch", "dev"),
            ("head_repository", {"full_name": "untrusted/repo"}),
            ("event", "pull_request"),
            ("path", ".github/workflows/other.yml"),
        ):
            with self.subTest(key=key):
                self.run = {**original, key: value}
                with (
                    patch.object(source, "api", side_effect=self.api),
                    self.assertRaisesRegex(ValueError, "Untrusted"),
                ):
                    source.verification(self.commit)

    def test_no_proof_or_skipped_tests_require_fresh_verification(self):
        with patch.object(source, "api", return_value={"workflow_runs": []}):
            self.assertEqual(source.verification(self.commit)["mode"], "fresh")
        self.job["steps"][0]["conclusion"] = "skipped"
        with patch.object(source, "api", side_effect=self.api):
            self.assertEqual(source.verification(self.commit)["mode"], "fresh")

    def test_pending_job_is_observed_instead_of_duplicating_it(self):
        self.job["status"] = "in_progress"

        def complete(_seconds):
            self.job["status"] = "completed"

        with (
            patch.object(source, "api", side_effect=self.api),
            patch.object(source.time, "sleep", side_effect=complete) as sleep,
        ):
            self.assertEqual(source.verification(self.commit)["mode"], "reused")
        sleep.assert_called_once()
        self.job["status"] = "in_progress"
        with (
            patch.object(source, "api", side_effect=self.api),
            self.assertRaisesRegex(ValueError, "pending"),
        ):
            source.verification(self.commit, timeout=0)

    def test_failed_checks_stop_release(self):
        self.job["conclusion"] = "failure"
        with (
            patch.object(source, "api", side_effect=self.api),
            self.assertRaisesRegex(ValueError, "failed"),
        ):
            source.verification(self.commit)

    def test_reuse_does_not_run_the_suite_again_and_fresh_mode_runs_it_once(self):
        for mode, calls in (("reused", 0), ("fresh", 2)):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary) / "proof.json"
                proof = {"schema": 1, "source_commit": self.commit, "mode": mode}
                with (
                    patch.object(
                        sys, "argv", ["verify", "--commit", self.commit, "--output", str(output)]
                    ),
                    patch.object(source, "verification", return_value=proof),
                    patch.object(source.subprocess, "run") as run,
                ):
                    source.main()
                self.assertEqual(run.call_count, calls)
                self.assertEqual(read_json(output), proof)

    def test_bundled_report_requires_exact_client_and_bundled_runtime_success(self):
        from common import digest

        candidate = read_json(source.ROOT / "config/clients.lock.json")["clients"]["canary"]
        report = {
            "runtime_kind": "official bundled baseline",
            "all_selected_passed": True,
            "combinations": [
                {
                    "channel": "canary",
                    "passed": True,
                    "native_fixture_passed": True,
                    "offline_krisp_verified": True,
                    "client_digest": digest(candidate),
                }
            ],
        }
        source.validate_bundled_report(report, "canary", candidate)
        for field, value in (("runtime_kind", "custom"), ("all_selected_passed", False)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                source.validate_bundled_report({**report, field: value}, "canary", candidate)
        for field, value in (
            ("passed", False),
            ("native_fixture_passed", False),
            ("offline_krisp_verified", False),
            ("client_digest", "b" * 64),
        ):
            changed = copy.deepcopy(report)
            changed["combinations"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                source.validate_bundled_report(changed, "canary", candidate)

    def test_bundled_reuse_downloads_exact_evidence_and_changed_inputs_require_fresh_check(self):
        from common import digest, write_json

        candidate = read_json(source.ROOT / "config/clients.lock.json")["clients"]["canary"]
        report = {
            "runtime_kind": "official bundled baseline",
            "all_selected_passed": True,
            "combinations": [
                {
                    "channel": "canary",
                    "passed": True,
                    "native_fixture_passed": True,
                    "offline_krisp_verified": True,
                    "client_digest": digest(candidate),
                }
            ],
        }
        job = {
            "id": 56,
            "name": "native-baselines / validate",
            "status": "completed",
            "conclusion": "success",
        }

        def download(command, **kwargs):
            directory = Path(command[command.index("--dir") + 1])
            write_json(directory / "native-runtime/runtime-checks.json", report)

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(
                source,
                "verification",
                return_value={"mode": "reused", "checks_run": 12, "source_commit": self.commit},
            ),
            patch.object(source, "api", return_value={"jobs": [job]}),
            patch.object(source.subprocess, "run", side_effect=download),
        ):
            output = Path(temporary) / "reuse.json"
            proof = source.bundled_verification(self.commit, candidate, "canary", output)
            self.assertTrue(proof["reused"])
            self.assertEqual(
                read_json(output.parent / "native-runtime/runtime-checks.json"), report
            )
        with patch.object(source, "verification") as checks:
            proof = source.bundled_verification(
                self.commit, {**candidate, "version": "1.0.1"}, "canary", Path("/unused/proof.json")
            )
            self.assertEqual(proof["mode"], "fresh")
            checks.assert_not_called()
