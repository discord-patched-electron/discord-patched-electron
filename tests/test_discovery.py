import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import ROOT, read_json
from discover import GitHub, parse_tags, pending, reconcile

NOW = "2026-10-23T20:00:00Z"


def release(version, identity, published="2026-10-22T12:00:00Z"):
    return {"tag_name": "v" + version, "id": identity, "draft": False,
            "prerelease": "-" in version, "published_at": published,
            "repository": "electron/nightlies" if "-nightly." in version else "electron/electron"}


class Discovery(unittest.TestCase):
    def setUp(self):
        self.sources = read_json(ROOT / "config/sources.lock.json")
        self.clients = read_json(ROOT / "config/clients.lock.json")["clients"]
        self.records = [release("45.0.0", 1), release("46.0.0-alpha.1", 2),
                        release("47.0.0-nightly.20261023", 3), release("43.8.2", 4)]
        self.tags = {record["tag_name"]: f"{record['id']:040x}" for record in self.records}
        self.verify = Mock()

    def reconcile(self, state=None, records=None, clients=None):
        return reconcile(state, self.records if records is None else records, self.tags,
                         clients or self.clients, self.sources, NOW, self.verify)

    def add(self, version, identity, published="2026-10-23T20:01:00Z"):
        value = release(version, identity, published)
        self.records.append(value)
        self.tags[value["tag_name"]] = f"{identity:040x}"

    def test_activation_seeds_three_channels_without_historical_backfill(self):
        state = self.reconcile()
        self.assertEqual(len(state["releases"]), 3)
        self.assertEqual(len(pending(state)), 3)
        self.assertNotIn("stable:43.8.2", state["releases"])
        self.assertEqual(self.verify.call_count, 3)
        self.assertEqual(self.reconcile(state), state)
        self.assertEqual(self.verify.call_count, 3)

    def test_multiple_stables_maintenance_and_prereleases_survive_missed_polls(self):
        before = self.reconcile()
        for version, identity in [("45.0.1", 10), ("45.0.2", 11), ("43.8.3", 12),
                                  ("46.0.0-alpha.2", 13), ("46.0.0-beta.1", 14)]:
            self.add(version, identity)
        state = self.reconcile(before)
        ordered = [state["runtimes"][key]["target"]["version"] for key in pending(state)]
        self.assertEqual(ordered[:4], ["43.8.3", "45.0.0", "45.0.1", "45.0.2"])
        self.assertIn("46.0.0-alpha.2", ordered)
        self.assertIn("46.0.0-beta.1", ordered)
        self.assertEqual(self.reconcile(state), state)

    def test_pending_nightlies_coalesce_but_running_nightly_is_preserved(self):
        state = self.reconcile()
        old = state["releases"]["nightly:47.0.0-nightly.20261023"]["runtime_key"]
        self.add("47.0.0-nightly.20261024", 10)
        self.add("47.0.0-nightly.20261025", 11)
        coalesced = self.reconcile(state)
        self.assertEqual(coalesced["runtimes"][old]["status"], "superseded")
        selected = [coalesced["runtimes"][key] for key in pending(coalesced)]
        self.assertEqual([item["target"]["version"] for item in selected if item["channel"] == "nightly"],
                         ["47.0.0-nightly.20261025"])
        state["runtimes"][old]["status"] = "building"
        self.assertEqual(self.reconcile(state)["runtimes"][old]["status"], "building")

    def test_moved_and_missing_tags_do_not_replace_pinned_bytes(self):
        state = self.reconcile()
        original = state["releases"]["stable:45.0.0"]["commit"]
        self.tags["v45.0.0"] = "f" * 40
        del self.tags["v46.0.0-alpha.1"]
        moved = self.reconcile(state)
        self.assertEqual(moved["releases"]["stable:45.0.0"]["status"], "tag-moved")
        self.assertEqual(moved["releases"]["stable:45.0.0"]["commit"], original)
        self.assertEqual(moved["releases"]["prerelease:46.0.0-alpha.1"]["status"], "source-tag-missing")
        self.assertEqual(len(pending(moved)), 1)
        self.tags["v45.0.0"] = original
        self.assertEqual(self.reconcile(moved)["releases"]["stable:45.0.0"]["status"], "tag-moved")

    def test_withdrawn_or_replaced_release_is_not_queued(self):
        state = self.reconcile()
        removed = self.reconcile(state, records=self.records[1:])
        self.assertEqual(removed["releases"]["stable:45.0.0"]["status"], "withdrawn")
        self.records[0]["id"] = 99
        replaced = self.reconcile(state)
        self.assertEqual(replaced["releases"]["stable:45.0.0"]["status"], "release-replaced")
        self.assertEqual(len(pending(replaced)), 2)

    def test_release_id_cannot_be_silently_retagged(self):
        state = self.reconcile()
        self.records[0] = release("45.0.1", 1, "2026-10-23T20:01:00Z")
        self.tags["v45.0.1"] = "f" * 40
        updated = self.reconcile(state)
        self.assertEqual(updated["releases"]["stable:45.0.0"]["status"], "release-replaced")
        self.assertNotIn("stable:45.0.1", updated["releases"])
        self.assertEqual(len(pending(updated)), 2)

    def test_bad_new_nightly_does_not_block_stable_discovery(self):
        state = self.reconcile()
        self.add("45.0.1", 10)
        self.add("47.0.0-nightly.20261024", 11)
        def verify(commit):
            if commit == f"{11:040x}":
                raise ValueError("not a commit")
        self.verify.side_effect = verify
        updated = self.reconcile(state)
        self.assertEqual(updated["releases"]["nightly:47.0.0-nightly.20261024"]["status"], "invalid-source")
        self.assertIn(updated["releases"]["stable:45.0.1"]["runtime_key"], pending(updated))

    def test_client_only_change_does_not_queue_more_native_builds(self):
        state = self.reconcile()
        for runtime in state["runtimes"].values():
            runtime["status"] = "compiled"
        clients = copy.deepcopy(self.clients)
        clients["canary"]["sources"]["discord_krisp"]["sha256"] = "b" * 64
        updated = self.reconcile(state, clients=clients)
        self.assertEqual(state["runtimes"], updated["runtimes"])
        self.assertEqual(pending(updated), [])
        self.assertEqual(state["clients"]["stable"], updated["clients"]["stable"])
        self.assertNotEqual(state["clients"]["canary"], updated["clients"]["canary"])

    def test_same_failed_key_is_not_automatically_retried(self):
        state = self.reconcile()
        key = pending(state)[0]
        state["runtimes"][key].update(status="failed", failure_kind="incompatible-patch")
        self.assertNotIn(key, pending(self.reconcile(state)))

    def test_unknown_new_version_format_is_recorded_not_silently_dropped(self):
        state = self.reconcile()
        self.add("46.0.0-rc.1", 10)
        updated = self.reconcile(state)
        self.assertIn("electron/electron:10", updated["issues"])
        self.assertEqual(len(pending(updated)), 3)

    def test_annotated_and_lightweight_tags(self):
        parsed = parse_tags("a" * 40 + "\trefs/tags/v45.0.0\n" +
                            "b" * 40 + "\trefs/tags/v45.0.0^{}\n" +
                            "c" * 40 + "\trefs/tags/v46.0.0-alpha.1\n")
        self.assertEqual(parsed["v45.0.0"], "b" * 40)
        self.assertEqual(parsed["v46.0.0-alpha.1"], "c" * 40)

    def test_release_pagination_does_not_stop_on_old_publication_dates(self):
        github = GitHub()
        old = release("43.0.0", 1)
        old.pop("repository")
        new = release("45.0.1", 2, "2026-10-23T21:00:00Z")
        new.pop("repository")
        github.get = Mock(side_effect=[[old] * 100, [new]])
        records = github.releases("electron/electron")
        self.assertEqual(len(records), 101)
        self.assertEqual(records[-1]["tag_name"], "v45.0.1")
        self.assertIn("page=2", github.get.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
