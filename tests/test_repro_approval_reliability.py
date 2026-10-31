"""REPRODUCTION: passing assertions of baseline approval/recovery defects.

Reuse existing disposable-key fixtures. Never launch the fixture application or
Electron; module preparation stops at the existing synthetic install boundary.
"""

import os
import unittest
from unittest.mock import patch

import test_runtime_updater as runtimes


class ApprovalRollbackReproduction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        runtimes.RuntimeUpdates.setUpClass()

    @classmethod
    def tearDownClass(cls):
        runtimes.RuntimeUpdates.tearDownClass()

    def setUp(self):
        self.fixture = runtimes.RuntimeUpdates()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()

    def test_repro_offline_rollback_ignores_intact_package_pinned_client(self):
        """REPRODUCTION: rolling back the application cannot select cached baseline modules."""
        fixture = self.fixture
        old_version = fixture.policy["release_version"]
        new_version = fixture.manifest["version"]
        fixture.manifest["application"] = {
            "bootstrap_api": 1,
            "asset": "application.zip",
            "version": new_version,
            "archive_sha256": "d" * 64,
            "index_sha256": "e" * 64,
        }
        fixture.sign()
        updated_app = fixture.root / "new application"
        updated_app.mkdir()
        runtimes.write_json(updated_app / "client-update.json", fixture.manifest["client"])
        runtimes.write_json(
            updated_app / "runtime-update.json",
            {
                **fixture.policy,
                "release_version": new_version,
                "electron_version": fixture.manifest["electron_version"],
                "runtime_key": fixture.manifest["runtime"]["runtime_key"],
                "archive_sha256": fixture.manifest["runtime"]["archive_sha256"],
            },
        )
        key = updated_app / "integration/updater/release-signing-key.asc"
        key.parent.mkdir(parents=True)
        key.write_bytes(fixture.public)
        with fixture.modules() as (root, downloaded):
            # A working old application caches its package-pinned client. The
            # signed newer release is correctly excluded by the application cap.
            with patch.dict(os.environ, {"DPE_APPLICATION_VERSION": old_version}):
                baseline = runtimes.client.ensure_client(root, "canary")
            self.assertEqual(
                runtimes.client.read_object(baseline / "client.json")["version"], "1.0.123"
            )
            (root / "approvals/last-check.json").unlink()
            # The new application can now prepare its approved newer client;
            # model a subsequent startup failure by returning to the old version.
            with (
                patch.dict(os.environ, {"DPE_APPLICATION_VERSION": new_version}),
                patch.object(runtimes.client, "PACKAGE_ROOT", updated_app),
            ):
                updated = runtimes.client.ensure_client(root, "canary")
            self.assertNotEqual(updated, baseline)
            self.assertEqual(
                runtimes.client.read_object(updated / "client.json")["version"], "1.0.124"
            )
            count = len(downloaded)
            with (
                patch.dict(os.environ, {"DPE_APPLICATION_VERSION": old_version}),
                patch.object(
                    runtimes.runtime, "download", side_effect=AssertionError("offline network")
                ),
            ):
                runtimes.client.validate_host(root, "canary", baseline.name)
                sources = runtimes.client.approved_sources(
                    root, "canary", offline=True, previous=updated
                )
                self.assertEqual(len(sources), 1)
                self.assertEqual(
                    runtimes.client.pins(sources[0]),
                    runtimes.client.pins(runtimes.client.read_object(baseline / "client.json")),
                )
                # WRONG: a complete, authorized offline fallback exists, but only
                # the newer pointer is considered and startup is rejected.
                with self.assertRaisesRegex(ValueError, "First launch requires an internet"):
                    runtimes.client.ensure_client(root, "canary", offline=True)
                self.assertEqual(runtimes.client.current_host(root, "canary"), updated)
                # Control: online-mode selection recovers the exact old cache
                # with no downloads (the successful check is still throttled).
                self.assertEqual(runtimes.client.ensure_client(root, "canary"), baseline)
                self.assertEqual(len(downloaded), count)
                self.assertEqual(
                    runtimes.client.ensure_client(root, "canary", offline=True), baseline
                )
