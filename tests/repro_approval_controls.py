"""Offline approval review controls; these assert expected baseline protections."""

import os
import shutil
import subprocess
import unittest
from unittest.mock import patch

import test_application_updates as apps
import test_runtime_updater as runtimes


class SignedReleaseControls(unittest.TestCase):
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
        self.key = self.fixture.app / "integration/updater/release-signing-key.asc"
        runtimes.write_json(self.fixture.release / "github.json", self.fixture.github)
        runtimes.write_json(self.fixture.release / "approval.json", self.fixture.approval)

    def verify(self):
        return runtimes.runtime.approved_release(
            self.fixture.release, self.fixture.policy, self.key
        )

    def test_manual_approval_fields_must_match(self):
        for field, value in (
            ("schema", 2),
            ("tag", "different-tag"),
            ("version", "20261024.120000"),
            ("manifest_sha256", "0" * 64),
            ("approved_by", "  "),
        ):
            with self.subTest(field=field):
                runtimes.write_json(
                    self.fixture.release / "approval.json", {**self.fixture.approval, field: value}
                )
                with self.assertRaisesRegex(ValueError, "manual approval"):
                    self.verify()

    def test_mixed_manifest_and_signature_fail_closed(self):
        self.fixture.manifest["minimum_glibc"] = "2.2"
        runtimes.write_json(self.fixture.release / "release.json", self.fixture.manifest)
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            self.verify()

    def test_published_release_and_runtime_identity_must_match(self):
        for field in ("draft", "prerelease"):
            with self.subTest(field=field):
                runtimes.write_json(
                    self.fixture.release / "github.json", {**self.fixture.github, field: True}
                )
                with self.assertRaisesRegex(ValueError, "published approved"):
                    self.verify()
        runtimes.write_json(self.fixture.release / "github.json", self.fixture.github)
        self.fixture.manifest["runtime"]["version"] = "47.0.0-alpha.1"
        self.fixture.sign()
        runtimes.write_json(self.fixture.release / "approval.json", self.fixture.approval)
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.verify()

    def test_superseding_remote_approval_does_not_revoke_retained_offline_proof(self):
        # Retained copies are deliberately allowed by approved_release's docstring.
        self.fixture.approval = {**self.fixture.approval, "version": "20261026.120000"}
        with patch.object(runtimes.runtime, "download", side_effect=AssertionError("network")):
            self.assertEqual(self.verify(), self.fixture.manifest)

    def test_valid_historical_signature_remains_usable_after_key_expiry(self):
        # Generate/sign using the existing fixture routine at a fixed past time.
        # Only this disposable key's GPG commands receive a synthetic clock.
        run = subprocess.run

        def past_signer(command, **kwargs):
            if command[0] == "gpg":
                command = [command[0], "--faked-system-time", "20260101T000000", *command[1:]]
            return run(command, **kwargs)

        class PastFixture(runtimes.RuntimeUpdates):
            pass

        with patch.object(subprocess, "run", side_effect=past_signer):
            PastFixture.setUpClass()
        self.addCleanup(PastFixture.tearDownClass)
        self.fixture.signer = PastFixture.signer
        self.key.write_bytes(PastFixture.public)
        with patch.object(subprocess, "run", side_effect=past_signer):
            self.fixture.sign()
        runtimes.write_json(self.fixture.release / "approval.json", self.fixture.approval)
        # gpgv checks the actual signature at the current time, after key expiry.
        self.assertEqual(self.verify(), self.fixture.manifest)

    def test_version_order_handles_midnight_and_prerelease_revision_numbers(self):
        runtime = runtimes.runtime
        self.assertLess(
            runtime.release_version("20261025.235959"), runtime.release_version("20261026.000001")
        )
        self.assertLess(
            runtime.electron_version("46.0.0-alpha.9"), runtime.electron_version("46.0.0-alpha.10")
        )
        self.assertLess(
            runtime.electron_version("46.0.0-alpha.10"), runtime.electron_version("46.0.0-beta.1")
        )


class LoaderRevalidationControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apps.ApplicationUpdates.setUpClass()

    @classmethod
    def tearDownClass(cls):
        apps.ApplicationUpdates.tearDownClass()

    def setUp(self):
        self.fixture = apps.ApplicationUpdates()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()

    def test_changed_proof_between_updater_validation_and_loader_staging_is_rejected(self):
        fixture = self.fixture
        second, archive = fixture.payload("second", "20261027.130000")
        proof = fixture.approve(second)
        manifest = apps.runtime.approved_release(
            proof,
            fixture.policy,
            fixture.package / "release-signing-key.asc",
            application_update=True,
        )
        self.assertEqual(manifest["application"], second)
        record = apps.read_json(proof / "approval.json")
        apps.write_json(proof / "approval.json", {**record, "manifest_sha256": "0" * 64})
        with self.assertRaisesRegex(ValueError, "no matching release approval"):
            apps.loader.install(
                fixture.root, fixture.package, fixture.policy, second, archive, proof
            )
        self.assertFalse((fixture.root / "generations" / second["archive_sha256"]).exists())

    def test_application_prepare_publishes_retained_evidence_atomically(self):
        fixture = self.fixture
        proof = fixture.approve(fixture.first)
        original_copy = shutil.copyfile

        def download(url, target, **_kwargs):
            name = (
                "github.json"
                if url.endswith("/latest")
                else "approval.json"
                if "/config/" in url
                else url.rsplit("/", 1)[-1]
            )
            target.write_bytes((proof / name).read_bytes())

        def failing_copy(src, dst, **kwargs):
            if dst.name == "SHA256SUMS.sig":
                raise OSError("synthetic write failure")
            return original_copy(src, dst, **kwargs)

        with (
            patch.dict(
                os.environ,
                {
                    "DPE_INSTALL_ROOT": str(fixture.package),
                    "DPE_CLIENT_OFFLINE": "0",
                    "DPE_CLIENT_PINNED": "0",
                    "DPE_APPLICATION_UPDATES": "1",
                },
            ),
            patch.object(apps.runtime, "download", side_effect=download),
        ):
            with patch.object(shutil, "copyfile", side_effect=failing_copy):
                self.assertIsNone(apps.application.prepare(fixture.first_app, fixture.profile))
            self.assertFalse((fixture.first_app.parent / "proof").exists())
            (fixture.first_app.parent / "last-check.json").unlink()
            self.assertIsNone(apps.application.prepare(fixture.first_app, fixture.profile))
            self.assertTrue((fixture.first_app.parent / "proof/SHA256SUMS.sig").is_file())
