import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from blacksmith_checkpoint_retention import managed_keys
from state_branch import PROJECT


class Retention(unittest.TestCase):
    def test_only_managed_main_branch_snapshots_are_selected_across_all_pages(self):
        key = "ninja-v2-incremental-" + "a" * 64 + "-" + "b" * 64 + "-123"
        calls = []

        def fetch(*args):
            calls.append(args)
            page = int(args[args.index("--page") + 1])
            return {
                "repo": PROJECT,
                "installation": {"installation_name": "discord-patched-electron"},
                "entries": [
                    {"key": key, "scope": "refs/heads/main"},
                    {"key": key + "4", "scope": "refs/heads/feature"},
                    {"key": "another-cache", "scope": "refs/heads/main"},
                    {"key": "ninja-v2-unrecognized", "scope": "refs/heads/main"},
                ],
                "pagination": {"current_page": page, "has_more_pages": page == 1},
            }

        self.assertEqual(managed_keys(fetch), [key])
        self.assertEqual(len(calls), 2)

    def test_wrong_repository_is_rejected_before_any_deletion(self):
        with self.assertRaisesRegex(ValueError, "Wrong provider"):
            managed_keys(lambda *_: {"repo": "other/repo"})
