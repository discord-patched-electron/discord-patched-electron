import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ci_pr_evidence as evidence


class PrEvidence(unittest.TestCase):
    def setUp(self):
        self.repo = "owner/repo"
        self.proof = {"schema": 1, "tree": "merge-tree", "week": "2026-48", "selected": ["audio"]}
        self.pull = {
            "head": {"sha": "pr-head", "repo": {"full_name": self.repo}},
            "base": {"ref": "main"},
            "number": 42,
            "merged_at": "now",
            "merge_commit_sha": "merge",
        }
        self.run = {
            "id": 9,
            "path": evidence.WORKFLOW,
            "head_sha": "pr-head",
            "event": "pull_request",
            "head_repository": {"full_name": self.repo},
            "repository": {"full_name": self.repo},
            "created_at": datetime.now(UTC).isoformat(),
            "run_attempt": 1,
            "status": "completed",
            "conclusion": "success",
        }
        self.gate = {"name": "Checks complete", "conclusion": "success"}

    def api(self, repo, endpoint):
        self.assertEqual(repo, self.repo)
        if endpoint == "commits/merge/pulls":
            return [self.pull]
        if "/runs?" in endpoint:
            return {"workflow_runs": [self.run]}
        if endpoint == "actions/runs/9/attempts/1/jobs?per_page=100":
            return {"jobs": [self.gate]}
        self.fail(f"Unexpected API endpoint {endpoint}")

    def reuse(self, **changes):
        with (
            patch.object(evidence, "api", side_effect=self.api),
            patch.object(evidence, "download", return_value={**self.proof, "pull": 42, **changes}),
        ):
            return evidence.matching_evidence(self.repo, "merge", self.proof, wait_seconds=0)

    def test_completed_same_tree_and_scope_can_qualify_merge(self):
        self.assertEqual(self.reuse(), 9)
        self.assertEqual(self.reuse(selected=["audio", "source"]), 9)

    def test_missing_or_stale_evidence_keeps_checks_selected(self):
        for changes in (
            {"tree": "other-tree"},
            {"selected": ["source"]},
            {"week": "2026-47"},
            {"pull": 99},
        ):
            with self.subTest(changes=changes):
                self.assertIsNone(self.reuse(**changes))

    def test_foreign_pr_pending_run_and_failed_gate_cannot_qualify_merge(self):
        self.pull["head"]["repo"]["full_name"] = "fork/repo"
        self.assertIsNone(self.reuse())
        self.pull["head"]["repo"]["full_name"] = self.repo
        self.run["status"] = "in_progress"
        self.assertIsNone(self.reuse())
        self.run["status"] = "completed"
        self.gate["conclusion"] = "failure"
        self.assertIsNone(self.reuse())
