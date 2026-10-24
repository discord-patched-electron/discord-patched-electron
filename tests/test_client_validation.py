import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check_runtime_clients import check
from client_inputs import load_clients
from common import ROOT, digest, read_json, write_json
from fetch_client_snapshot import PROJECT, fetch
from smoke_packages import smoke


class ClientValidation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.snapshot = read_json(ROOT / "config/clients.lock.json")
        self.lock = self.root / "clients.json"
        write_json(self.lock, self.snapshot)

    def test_selection_requires_distinct_known_channels(self):
        self.assertEqual(list(load_clients(self.lock, ["canary"])), ["canary"])
        self.assertEqual(list(load_clients(self.lock)), ["canary"])
        for selection in ([], ["stable"], ["ptb"], ["stable", "stable"], ["unknown"]):
            with self.assertRaises(ValueError):
                load_clients(self.lock, selection)

    @patch("client_inputs.enabled_discord_channels", return_value=("stable", "ptb", "canary"))
    def test_failed_native_channel_preserves_other_selected_results(self, _channels):
        output = self.root / "native"
        with (
            patch("check_runtime_clients.generate"),
            patch(
                "check_runtime_clients.check_channel",
                side_effect=[ValueError("native bridge rejected"), {"passed": True}],
            ) as probe,
        ):
            report = check(output, clients_file=self.lock, channels=["ptb", "canary"])
        self.assertEqual([call.args[0] for call in probe.call_args_list], ["ptb", "canary"])
        self.assertFalse(report["all_selected_passed"])
        self.assertFalse(report["publishable"])
        self.assertFalse((output / "stable").exists())
        self.assertEqual(read_json(output / "runtime-checks.json"), report)
        for channel, passed in [("ptb", False), ("canary", True)]:
            result = read_json(output / channel / "client-check.json")
            self.assertEqual(result["passed"], passed)
            self.assertEqual(result["client_digest"], digest(self.snapshot["clients"][channel]))

    @patch("client_inputs.enabled_discord_channels", return_value=("stable", "ptb", "canary"))
    def test_package_download_failure_and_one_failed_recipe_do_not_cancel_others(self, _channels):
        output = self.root / "packages"

        def download(url, target, expected=None):
            if target.name == "ptb-core.tar.br":
                raise ValueError("checksum mismatch")
            target.write_bytes(b"fixture")
            return target

        def package(directory, downloads, discord, client, electron, target):
            (directory / "src").mkdir()
            (directory / "pkg").mkdir()
            (directory / "dummy.pkg.tar").write_bytes(b"fixture")
            if discord == "canary":
                raise ValueError("recipe failed")
            return {}

        with (
            patch("smoke_packages.download", side_effect=download) as downloads,
            patch("smoke_packages.subprocess.check_output", return_value="test packages\n"),
            patch("smoke_packages.package_one", side_effect=package) as recipes,
        ):
            report = smoke(output, self.lock, ["stable", "ptb", "canary"])
        self.assertFalse(report["all_selected_passed"])
        self.assertEqual(len(report["combinations"]), 3)
        self.assertEqual(recipes.call_count, 2)
        self.assertEqual([call.args[2] for call in recipes.call_args_list], ["stable", "canary"])
        self.assertTrue(
            any(call.args[1].name.startswith("stable-") for call in downloads.call_args_list)
        )
        for item in report["combinations"]:
            if item["discord_channel"] == "ptb":
                self.assertEqual(item["phase"], "client-download")
            else:
                self.assertEqual(item["passed"], item["discord_channel"] == "stable")
            self.assertFalse(item["publishable"])
            self.assertFalse(item["runtime_validated"])
        self.assertFalse(list(output.glob("*/*.pkg.tar")))
        self.assertFalse(list(output.glob("*/src")))
        self.assertFalse(list(output.glob("*/pkg")))

    def test_snapshot_requires_immutable_project_commit_and_matching_identities(self):
        output = self.root / "fetched.json"
        commit = "1" * 40
        state = {**self.snapshot, "client_errors": {"ptb": "manifest unavailable"}}
        with (
            patch.dict("os.environ", {"GH_REPO": PROJECT}),
            patch(
                "fetch_client_snapshot.subprocess.check_output", return_value=json.dumps(state)
            ) as api,
        ):
            with self.assertRaisesRegex(ValueError, "immutable"):
                fetch("ci-state", output)
            api.assert_not_called()
            fetch(commit, output)
            self.assertIn("state.json?ref=" + commit, api.call_args.args[0][-1])
            self.assertEqual(
                read_json(output),
                {"schema": 1, "clients": {"canary": self.snapshot["clients"]["canary"]}},
            )
            provenance = read_json(output.with_suffix(".provenance.json"))
            self.assertEqual(provenance["client_errors"], state["client_errors"])
            self.assertFalse(provenance["publishable"])
            invalid = copy.deepcopy(state)
            invalid["clients"]["ptb"] = invalid["clients"]["canary"]
            api.return_value = json.dumps(invalid)
            with self.assertRaisesRegex(ValueError, "different Discord channel"):
                fetch(commit, self.root / "invalid.json")
            self.assertFalse((self.root / "invalid.json").exists())
        with (
            patch.dict("os.environ", {"GH_REPO": "elsewhere/project"}),
            patch("fetch_client_snapshot.subprocess.check_output") as api,
        ):
            with self.assertRaisesRegex(ValueError, "restricted"):
                fetch(commit, output)
            api.assert_not_called()


if __name__ == "__main__":
    unittest.main()
