import base64
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from release_proposals import record_proposal


def stored(proposal):
    return {"sha": "blob", "content": base64.b64encode(json.dumps(proposal).encode()).decode()}


HEAD = {"object": {"sha": "1" * 40}}


class ReleaseProposals(unittest.TestCase):
    def setUp(self):
        self.proposal = {
            "schema": 1,
            "tag": "candidate",
            "version": "20261026.120000",
            "channel": "canary",
            "client_version": "1.0.2374",
            "electron_version": "46.0.0-alpha.1",
            "clients_state": "a" * 40,
            "source_commit": "b" * 40,
            "client": {"sources": {"discord_voice": {"sha256": "c" * 64}}},
            "runtime": {"run": "123", "archive_sha256": "d" * 64},
        }

    def test_proposal_commits_exact_inputs_on_main_with_a_descriptive_message(self):
        api = Mock(side_effect=[HEAD, None, {"commit": {"sha": "e" * 40}}])
        self.assertEqual(record_proposal(self.proposal, api), "e" * 40)
        args = api.call_args.kwargs
        self.assertEqual(args["method"], "PUT")
        self.assertEqual(args["data"]["branch"], "main")
        self.assertIn("Propose Discord Canary 1.0.2374", args["data"]["message"])
        self.assertEqual(json.loads(base64.b64decode(args["data"]["content"])), self.proposal)

    def test_retries_reuse_the_original_proposal_commit_without_writes(self):
        api = Mock(side_effect=[HEAD, stored(self.proposal), [{"sha": "e" * 40}]])
        self.assertEqual(record_proposal(self.proposal, api), "e" * 40)
        self.assertTrue(all("method" not in call.kwargs for call in api.call_args_list))
        self.assertIn("?ref=" + "1" * 40, api.call_args_list[1].args[0])
        self.assertIn("?sha=" + "1" * 40, api.call_args_list[2].args[0])

    def test_same_host_version_module_update_is_named_and_uses_blob_compare_and_swap(self):
        previous = copy.deepcopy(self.proposal)
        previous["tag"] = "older-candidate"
        previous["version"] = "20261025.120000"
        previous["client"]["sources"]["discord_voice"]["sha256"] = "f" * 64
        api = Mock(side_effect=[HEAD, stored(previous), {"commit": {"sha": "e" * 40}}])
        record_proposal(self.proposal, api)
        self.assertIn("(module update)", api.call_args.kwargs["data"]["message"])
        self.assertEqual(api.call_args.kwargs["data"]["sha"], "blob")

    def test_changed_inputs_for_an_existing_tag_are_rejected(self):
        changed = {**self.proposal, "clients_state": "f" * 40}
        api = Mock(side_effect=[HEAD, stored(changed)])
        with self.assertRaisesRegex(ValueError, "different inputs"):
            record_proposal(self.proposal, api)
        self.assertEqual(api.call_count, 2)

    def test_retry_of_an_older_proposal_never_overwrites_the_latest_record(self):
        newer = {**self.proposal, "tag": "newer", "version": "20261027.120000"}
        api = Mock(side_effect=[HEAD, stored(newer), [{"sha": "e" * 40}], stored(self.proposal)])
        self.assertEqual(record_proposal(self.proposal, api), "e" * 40)
        self.assertTrue(all("method" not in call.kwargs for call in api.call_args_list))
        api = Mock(side_effect=[HEAD, stored(newer), []])
        with self.assertRaisesRegex(ValueError, "newer proposal"):
            record_proposal(self.proposal, api)
