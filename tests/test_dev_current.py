"""Current-checkout cache invalidation and safe publication."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dev_current
from common import read_json, write_json
from dev import Manager, lock


class CurrentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        subprocess.run(["git", "init", "-q", "-b", "dev", str(self.root)], check=True)
        self.helper = self.root / "integration/desktop/popout.cjs"
        self.helper.parent.mkdir(parents=True)
        self.helper.write_text("helper one")
        self.frozen = self.root / "native/fixture.rs"
        self.frozen.parent.mkdir()
        self.frozen.write_text("native one")
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        self.addCleanup(patch.stopall)
        patch("dev.ROOT", self.root).start()
        patch("dev_current.ROOT", self.root).start()
        self.manager = Manager(self.root / "out/dev")

    def test_content_inventory_branch_and_build_boundaries(self):
        initial = dev_current.checkout()
        self.helper.write_text("helper two")
        helper = dev_current.checkout()
        self.assertEqual(initial["build"], helper["build"])
        self.assertNotEqual(initial["helpers"], helper["helpers"])
        self.frozen.write_text("native two")
        self.assertNotEqual(initial["build"], dev_current.checkout()["build"])
        self.frozen.write_text("native one")
        self.helper.unlink()
        self.assertNotEqual(initial["build"], dev_current.checkout()["build"])
        self.helper.write_text("helper one")
        added = self.helper.with_name("new.cjs")
        added.write_text("untracked helper")
        self.assertNotEqual(initial["build"], dev_current.checkout()["build"])
        added.unlink()
        subprocess.run(
            ["git", "symbolic-ref", "HEAD", "refs/heads/other"], cwd=self.root, check=True
        )
        self.assertEqual(dev_current.checkout()["branch"], "other")
        self.assertEqual(initial["build"], dev_current.checkout()["build"])

    def prepare_mocks(self):
        artifact = self.root / "out/compiler-artifact"
        artifact.mkdir(parents=True)
        write_json(artifact / "artifact-provenance.json", {})
        patch("dev_current.verified_artifact", return_value="runtime-one").start()

        def create(label, *_):
            app = self.manager.path("versions", label)
            app.mkdir()
            write_json(app / "dev-version.json", {"schema": 1})

        return patch.object(self.manager, "create", side_effect=create), patch.object(
            self.manager, "sync"
        )

    def test_reuses_build_and_helper_cache_but_rebuilds_frozen_inputs(self):
        create_patch, sync_patch = self.prepare_mocks()
        with create_patch as create, sync_patch as sync:
            first = dev_current.resolve(self.manager)
            self.assertEqual(dev_current.resolve(self.manager), first)
            self.assertEqual(create.call_count, 1)
            self.assertEqual(sync.call_count, 1)
            self.helper.write_text("changed helper")
            self.assertEqual(dev_current.resolve(self.manager), first)
            self.assertEqual(create.call_count, 1)
            self.assertEqual(sync.call_count, 2)
            self.frozen.write_text("changed native")
            second = dev_current.resolve(self.manager)
            self.assertNotEqual(first, second)
            self.assertEqual(create.call_count, 2)
            self.frozen.write_text("native one")
            self.assertEqual(dev_current.resolve(self.manager), first)
            self.assertEqual(create.call_count, 2)

    def test_never_syncs_running_version_or_publishes_partial_refresh(self):
        create_patch, sync_patch = self.prepare_mocks()
        with create_patch, sync_patch as sync:
            label = dev_current.resolve(self.manager)
            record = self.manager.version(label) / "current-checkout.json"
            before = read_json(record)
            with lock(self.manager.home / "locks" / (label + ".version"), shared=True):
                self.assertEqual(dev_current.resolve(self.manager), label)
                self.helper.write_text("new")
                with self.assertRaisesRegex(ValueError, "In use"):
                    dev_current.resolve(self.manager)
            sync.side_effect = ValueError("bundle failed")
            with self.assertRaisesRegex(ValueError, "bundle failed"):
                dev_current.resolve(self.manager)
            self.assertFalse(record.exists(), "A partial refresh must never remain a cache hit")
            sync.side_effect = None
            self.assertEqual(dev_current.resolve(self.manager), label)
            self.assertNotEqual(read_json(record), before)

    def test_no_matching_runtime_never_builds_or_launches_stale_named_current(self):
        with patch.object(self.manager, "create") as create:
            with self.assertRaisesRegex(ValueError, "No verified Electron artifact"):
                dev_current.resolve(self.manager)
            create.assert_not_called()

    def test_last_good_packaging_exception_cannot_hide_changed_native_inputs(self):
        capacity = {
            "source": {"target": "prerelease"},
            "runtime_key": "old",
            "archive_sha256": "archive",
        }
        provenance = {"capacity": capacity}

        def metadata(path):
            return (
                provenance
                if path.name == "artifact-provenance.json"
                else {"targets": {"prerelease": {}}}
            )

        with (
            patch("dev_current.read_json", side_effect=metadata),
            patch("dev_current.sha256", return_value="archive"),
            patch("fetch_reference.validate_provenance"),
            patch("fetch_reference.reviewed_compatibility", return_value=False),
            patch("build_reference.build_config", return_value={}),
            patch("runtime_fingerprints.runtime_key", return_value="current"),
        ):
            with self.assertRaisesRegex(ValueError, "native inputs"):
                dev_current.verified_artifact(self.root)
            capacity["runtime_key"] = "current"
            self.assertEqual(dev_current.verified_artifact(self.root), "current")
            capacity["archive_sha256"] = "damaged"
            with self.assertRaisesRegex(ValueError, "checksum"):
                dev_current.verified_artifact(self.root)

    def test_source_race_during_assembly_discards_only_the_new_payload(self):
        create_patch, sync_patch = self.prepare_mocks()
        with create_patch as create, sync_patch:
            original = create.side_effect

            def racing_create(*args):
                original(*args)
                self.frozen.write_text("changed during assembly")

            create.side_effect = racing_create
            with self.assertRaisesRegex(ValueError, "changed during assembly"):
                dev_current.resolve(self.manager)
            self.assertEqual(list((self.manager.home / "versions").iterdir()), [])

    def test_current_uses_dev_profile_and_checks_its_lock_before_building(self):
        with (
            patch("dev_current.resolve", return_value="tree-fixture") as resolve,
            patch.object(self.manager, "run_version", return_value=0) as run,
        ):
            self.assertEqual(self.manager.run("current", "dev", False, False, []), 0)
            run.assert_called_once_with("tree-fixture", "dev", False, False, [])
            with lock(self.manager.home / "locks/dev.profile"):
                with self.assertRaisesRegex(ValueError, "In use"):
                    self.manager.run("current", "dev", False, False, [])
            resolve.assert_called_once()


if __name__ == "__main__":
    unittest.main()
