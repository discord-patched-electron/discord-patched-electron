"""Publication must bind approval to reviewed bytes and one successful workflow."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import publish_working_tree as publish
from common import sha256


class WorkingTreePublication(unittest.TestCase):
    def test_plan_rejects_modified_or_unplanned_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "app.py").write_text("reviewed")
            plan = {
                "commits": [
                    {"message": "feat: Reviewed", "files": {"app.py": sha256(root / "app.py")}}
                ]
            }
            with (
                patch.object(publish, "ROOT", root),
                patch.object(publish, "changed_paths", return_value={"app.py"}),
            ):
                publish.validate_plan(plan)
                (root / "app.py").write_text("changed")
                with self.assertRaisesRegex(ValueError, "changed after review"):
                    publish.validate_plan(plan)
            with patch.object(publish, "changed_paths", return_value={"app.py", "secret"}):
                with self.assertRaisesRegex(ValueError, "paths differ"):
                    publish.validate_plan(plan)

    def test_failed_run_never_becomes_success(self):
        with patch.object(
            publish,
            "api",
            return_value={"status": "completed", "conclusion": "failure", "html_url": "fixture"},
        ):
            with self.assertRaisesRegex(RuntimeError, "Workflow failed"):
                publish.wait_run(42, float("inf"))

    def test_candidate_is_bound_to_run_and_clean_source(self):
        head = "a" * 40
        entries = [{"target_commitish": head}, {"target_commitish": "b" * 40}]
        manifest = {
            "workflow_run": "42",
            "source_commit": head,
            "source_dirty": False,
            "signing_required": True,
        }
        with (
            patch("releases.published_releases", return_value=entries),
            patch("releases.published_manifest", return_value=manifest),
            patch("releases.manifest_tag", return_value="exact-tag"),
        ):
            self.assertEqual(publish.candidate_for_run(42, head), "exact-tag")
            with self.assertRaisesRegex(ValueError, "exactly one"):
                publish.candidate_for_run(43, head)
            manifest["source_dirty"] = True
            with self.assertRaisesRegex(ValueError, "clean and signed"):
                publish.candidate_for_run(42, head)

    def test_ambiguous_dispatch_is_rejected_without_redispatch(self):
        state = {"release": {"head": "a" * 40, "before": [1]}}
        with (
            patch.object(
                publish,
                "api",
                return_value={
                    "workflow_runs": [{"id": n, "head_sha": "a" * 40} for n in (1, 2, 3)]
                },
            ),
            patch.object(publish, "command") as command,
        ):
            with self.assertRaisesRegex(RuntimeError, "Multiple matching"):
                publish.dispatch("release.yml", {}, state, Path("unused"), "release", float("inf"))
            command.assert_not_called()
