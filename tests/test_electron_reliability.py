"""Source-write races and advisory pagination must not lose update work."""

import base64
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from advance_electron import publish_source_lock
from discover import GitHub
from schedule_updates import read_run


def blob(value, sha="blob-sha"):
    return {"sha": sha, "content": base64.b64encode(json.dumps(value).encode()).decode()}


class SourceUpdateReliability(unittest.TestCase):
    def test_unrelated_main_race_rechecks_blob_and_retries(self):
        old, new = {"version": "old"}, {"version": "new"}
        with (
            patch(
                "advance_electron.api",
                side_effect=[
                    blob(old),
                    subprocess.CalledProcessError(1, "gh", stderr="HTTP 409"),
                    blob(old),
                    {},
                ],
            ) as api,
            patch("advance_electron.time.sleep"),
        ):
            publish_source_lock(old, new, "new")
        writes = [call.kwargs["data"] for call in api.call_args_list if "data" in call.kwargs]
        self.assertEqual(len(writes), 2)
        self.assertEqual(writes[0], writes[1])
        self.assertEqual(writes[0]["sha"], "blob-sha")

    def test_lost_response_is_idempotent(self):
        old, new = {"version": "old"}, {"version": "new"}
        with (
            patch(
                "advance_electron.api",
                side_effect=[
                    blob(old),
                    subprocess.CalledProcessError(1, "gh", stderr="HTTP 502"),
                    blob(new),
                ],
            ) as api,
            patch("advance_electron.time.sleep"),
        ):
            publish_source_lock(old, new, "new")
        self.assertEqual(api.call_count, 3)

    def test_changed_lock_is_never_overwritten(self):
        with patch("advance_electron.api", return_value=blob({"version": "other"})) as api:
            with self.assertRaisesRegex(ValueError, "changed concurrently"):
                publish_source_lock({"version": "old"}, {"version": "new"}, "new")
        self.assertEqual(api.call_count, 1)

    def test_deleted_workflow_does_not_hold_the_source_lease_forever(self):
        with patch("schedule_updates.project_api", return_value=None):
            self.assertEqual(read_run("123"), {"status": "completed", "conclusion": "deleted"})

    def test_advisories_paginate_and_reject_partial_snapshot(self):
        github = GitHub()
        with patch.object(
            github, "get", side_effect=[[{"id": i} for i in range(100)], [{"id": 100}]]
        ) as get:
            self.assertEqual(len(github.advisories()), 101)
        self.assertIn("page=2", get.call_args.args[0])
        with patch.object(github, "get", side_effect=[[{}] * 100, {"error": "bad page"}]):
            with self.assertRaises(ValueError):
                github.advisories()
