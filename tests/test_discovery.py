import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import ROOT, read_json
from discover import GitHub, parse_tags, pending, reconcile

NOW = "2026-10-23T20:00:00Z"


def release(version, identity, published="2026-10-22T12:00:00Z"):
    return {
        "tag_name": "v" + version,
        "id": identity,
        "draft": False,
        "prerelease": "-" in version,
        "published_at": published,
        "repository": "electron/nightlies" if "-nightly." in version else "electron/electron",
    }


class Discovery(unittest.TestCase):
    def setUp(self):
        self.sources = read_json(ROOT / "config/sources.lock.json")
        self.clients = read_json(ROOT / "config/clients.lock.json")["clients"]
        self.records = [
            release("45.0.0", 1),
            release("46.0.0-alpha.1", 2),
            release("47.0.0-nightly.20261023", 3),
            release("43.8.2", 4),
        ]
        self.tags = {record["tag_name"]: f"{record['id']:040x}" for record in self.records}
        self.verify = Mock()

    def reconcile(self, state=None, records=None, clients=None):
        return reconcile(
            state,
            self.records if records is None else records,
            self.tags,
            clients or self.clients,
            self.sources,
            NOW,
            self.verify,
        )

    def add(self, version, identity, published="2026-10-23T20:01:00Z"):
        value = release(version, identity, published)
        self.records.append(value)
        self.tags[value["tag_name"]] = f"{identity:040x}"

    def test_activation_seeds_only_prerelease_without_historical_backfill(self):
        state = self.reconcile()
        self.assertEqual(list(state["releases"]), ["prerelease:46.0.0-alpha.1"])
        self.assertEqual(len(pending(state)), 1)
        self.assertEqual(self.verify.call_count, 1)
        self.assertEqual(self.reconcile(state), state)
        self.assertEqual(self.verify.call_count, 1)

    def test_prerelease_history_survives_but_only_newest_pending_release_is_built(self):
        before = self.reconcile()
        for version, identity in [
            ("45.0.1", 10),
            ("45.0.2", 11),
            ("43.8.3", 12),
            ("46.0.0-alpha.2", 13),
            ("46.0.0-beta.1", 14),
            ("47.0.0-nightly.20261024", 15),
        ]:
            self.add(version, identity)
        state = self.reconcile(before)
        ordered = [state["runtimes"][key]["target"]["version"] for key in pending(state)]
        self.assertEqual(ordered, ["46.0.0-beta.1"])
        self.assertEqual(len(state["releases"]), 3)
        self.assertEqual(sum(r["status"] == "superseded" for r in state["runtimes"].values()), 2)
        self.assertEqual(self.reconcile(state), state)

    def test_disabled_pending_work_is_retired_without_changing_running_or_completed_work(self):
        from discover import normalize_release

        for status in ("pending", "building", "compiled", "failed"):
            with self.subTest(status=status):
                state = self.reconcile()
                for record in (self.records[0], self.records[2]):
                    release = normalize_release(record)
                    key = release["channel"] + "-legacy-key"
                    identity = release["channel"] + ":" + release["version"]
                    state["releases"][identity] = {
                        **release,
                        "commit": self.tags[release["tag"]],
                        "status": "available",
                        "runtime_key": key,
                    }
                    state["runtimes"][key] = {
                        "channel": release["channel"],
                        "target": release,
                        "status": status,
                        "attempts": [{"run": 123}],
                    }
                # Even an old ledger cannot dispatch disabled work before migration.
                self.assertEqual(len(pending(state)), 1)
                migrated = self.reconcile(state, records=[self.records[1]])
                for channel in ("stable", "nightly"):
                    runtime = migrated["runtimes"][channel + "-legacy-key"]
                    self.assertEqual(
                        runtime["status"], "disabled" if status == "pending" else status
                    )
                    self.assertEqual(runtime["attempts"], [{"run": 123}])
                self.assertEqual(migrated["releases"], state["releases"])
                self.assertEqual(self.reconcile(migrated), migrated)

    def test_moved_and_missing_tags_do_not_replace_pinned_bytes(self):
        state = self.reconcile()
        identity = "prerelease:46.0.0-alpha.1"
        original = state["releases"][identity]["commit"]
        del self.tags["v46.0.0-alpha.1"]
        missing = self.reconcile(state)
        self.assertEqual(missing["releases"][identity]["status"], "source-tag-missing")
        self.assertEqual(pending(missing), [])
        self.tags["v46.0.0-alpha.1"] = "f" * 40
        moved = self.reconcile(state)
        self.assertEqual(moved["releases"][identity]["status"], "tag-moved")
        self.assertEqual(moved["releases"][identity]["commit"], original)
        self.assertEqual(pending(moved), [])
        self.tags["v46.0.0-alpha.1"] = original
        self.assertEqual(self.reconcile(moved)["releases"][identity]["status"], "tag-moved")

    def test_withdrawn_or_replaced_release_is_not_queued(self):
        state = self.reconcile()
        removed = self.reconcile(state, records=[self.records[0], self.records[2]])
        self.assertEqual(removed["releases"]["prerelease:46.0.0-alpha.1"]["status"], "withdrawn")
        self.records[1]["id"] = 99
        replaced = self.reconcile(state)
        self.assertEqual(
            replaced["releases"]["prerelease:46.0.0-alpha.1"]["status"], "release-replaced"
        )
        self.assertEqual(pending(replaced), [])

    def test_release_id_cannot_be_silently_retagged(self):
        state = self.reconcile()
        self.records[1] = release("46.0.0-alpha.2", 2, "2026-10-23T20:01:00Z")
        self.tags["v46.0.0-alpha.2"] = "f" * 40
        updated = self.reconcile(state)
        self.assertEqual(
            updated["releases"]["prerelease:46.0.0-alpha.1"]["status"], "release-replaced"
        )
        self.assertNotIn("prerelease:46.0.0-alpha.2", updated["releases"])
        self.assertEqual(pending(updated), [])

    def test_invalid_source_does_not_block_another_prerelease(self):
        state = self.reconcile()
        self.add("46.0.0-alpha.2", 10)
        self.add("46.0.0-alpha.3", 11)

        def verify(commit):
            if commit == f"{11:040x}":
                raise ValueError("not a commit")

        self.verify.side_effect = verify
        updated = self.reconcile(state)
        self.assertEqual(
            updated["releases"]["prerelease:46.0.0-alpha.3"]["status"], "invalid-source"
        )
        self.assertIn(
            updated["releases"]["prerelease:46.0.0-alpha.2"]["runtime_key"], pending(updated)
        )

    def test_only_the_official_prerelease_feed_is_requested(self):
        from discover import release_repositories

        self.assertEqual(release_repositories(), ("electron/electron",))

    def test_client_only_change_does_not_queue_more_native_builds(self):
        state = self.reconcile()
        for runtime in state["runtimes"].values():
            runtime["status"] = "compiled"
        clients = copy.deepcopy(self.clients)
        clients["canary"]["sources"]["discord_krisp"]["sha256"] = "b" * 64
        updated = self.reconcile(state, clients=clients)
        self.assertEqual(state["runtimes"], updated["runtimes"])
        self.assertEqual(pending(updated), [])
        self.assertEqual(list(updated["clients"]), ["canary"])
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
        self.assertEqual(len(pending(updated)), 1)

    def test_annotated_and_lightweight_tags(self):
        parsed = parse_tags(
            "a" * 40
            + "\trefs/tags/v45.0.0\n"
            + "b" * 40
            + "\trefs/tags/v45.0.0^{}\n"
            + "c" * 40
            + "\trefs/tags/v46.0.0-alpha.1\n"
        )
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
