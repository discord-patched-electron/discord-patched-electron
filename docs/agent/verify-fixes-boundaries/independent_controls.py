"""Additional offline boundary controls; run from checkout root."""

import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path[:0] = [str(Path.cwd() / "tests"), str(Path.cwd() / "scripts")]
import test_discovery as discovery_fixture  # noqa: E402
import test_release_signing as signing_fixture  # noqa: E402
import test_update_policy as policy_fixture  # noqa: E402
from release_signing import sign, verify  # noqa: E402
from update_policy import plan  # noqa: E402


class SigningControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        signing_fixture.ReleaseSigning.setUpClass()
        cls.fixture = signing_fixture.ReleaseSigning()

    @classmethod
    def tearDownClass(cls):
        signing_fixture.ReleaseSigning.tearDownClass()

    def test_all_partial_artifact_subsets_recover_without_overwrite(self):
        names = ("app.pkg.tar.zst.sig", "SHA256SUMS.sig", "release-signing-key.asc")
        with patch("release_signing.PUBLIC_KEY", self.fixture.public):
            for mask in range(8):
                with self.subTest(mask=mask), tempfile.TemporaryDirectory() as temporary:
                    directory = Path(temporary) / "release"
                    self.fixture.release_fixture(directory)
                    kept = {}
                    for index, name in enumerate(names):
                        path = directory / name
                        if mask & (1 << index):
                            kept[name] = path.read_bytes()
                        else:
                            path.unlink()
                    sign(directory, self.fixture.secret)
                    verify(directory)
                    self.assertEqual(kept, {name: (directory / name).read_bytes() for name in kept})

    def test_final_verify_rejects_concurrent_bad_signature_without_overwrite(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("release_signing.PUBLIC_KEY", self.fixture.public),
        ):
            directory = Path(temporary) / "release"
            self.fixture.release_fixture(directory)
            signature = directory / "SHA256SUMS.sig"
            signature.unlink()
            link = os.link

            def collide(source, target):
                if target == signature:
                    signature.write_bytes(b"concurrent corrupted signature")
                    raise FileExistsError(str(target))
                return link(source, target)

            with (
                patch("release_signing.os.link", side_effect=collide),
                self.assertRaises(signing_fixture.subprocess.CalledProcessError),
            ):
                sign(directory, self.fixture.secret)
            self.assertEqual(signature.read_bytes(), b"concurrent corrupted signature")

    def test_mismatched_existing_key_rejects_retry_without_publishing_missing_signature(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("release_signing.PUBLIC_KEY", self.fixture.public),
        ):
            directory = Path(temporary) / "release"
            self.fixture.release_fixture(directory)
            (directory / "SHA256SUMS.sig").unlink()
            key = directory / "release-signing-key.asc"
            key.write_bytes(b"wrong public key")
            with self.assertRaisesRegex(ValueError, "pinned"):
                sign(directory, self.fixture.secret)
            self.assertEqual(key.read_bytes(), b"wrong public key")
            self.assertFalse((directory / "SHA256SUMS.sig").exists())


class DiscoveryControls(unittest.TestCase):
    def test_rejected_seed_blocks_backfill_but_keeps_new_release_discovery(self):
        for status in ("tag-moved", "release-replaced", "invalid-source", "source-tag-missing"):
            with self.subTest(status=status):
                fixture = discovery_fixture.Discovery()
                fixture.setUp()
                state = fixture.reconcile()
                identity = "prerelease:46.0.0-alpha.1"
                state["releases"][identity]["status"] = status
                history = discovery_fixture.release("46.0.0-alpha.0", 20)
                fixture.tags[history["tag_name"]] = "a" * 40
                fixture.add("46.0.0-alpha.2", 21)
                current = fixture.records[-1]
                updated = fixture.reconcile(state, records=[history, current])
                self.assertEqual(set(updated["releases"]), {identity, "prerelease:46.0.0-alpha.2"})
                self.assertEqual(updated["activated_at"], state["activated_at"])
                self.assertEqual(
                    updated["releases"][identity]["commit"], state["releases"][identity]["commit"]
                )
                if status in ("tag-moved", "release-replaced"):
                    self.assertEqual(updated["releases"][identity]["status"], status)
                self.assertEqual(
                    updated["releases"]["prerelease:46.0.0-alpha.2"]["status"], "available"
                )

    def test_no_seed_feed_preserves_failure_until_actual_first_observation(self):
        fixture = discovery_fixture.Discovery()
        fixture.setUp()
        previous = {
            "schema": 1,
            "activated_at": discovery_fixture.NOW,
            "releases": {},
            "runtimes": {},
            "issues": {},
        }
        original = copy.deepcopy(previous)
        with self.assertRaisesRegex(ValueError, "No published seed"):
            fixture.reconcile(previous, records=[])
        self.assertEqual(previous, original)
        recovered = fixture.reconcile(previous)
        self.assertEqual(list(recovered["releases"]), ["prerelease:46.0.0-alpha.1"])
        self.assertEqual(recovered["activated_at"], previous["activated_at"])


class AdvisoryControls(unittest.TestCase):
    def test_null_fix_does_not_hide_later_valid_advisory_or_override(self):
        fixture = policy_fixture.UpdatePolicy()
        fixture.setUp()
        vulnerability = {
            "package": {"ecosystem": "npm", "name": "electron"},
            "patched_versions": "46.0.0-alpha.2",
            "vulnerable_version_range": "< 46.0.0-alpha.2",
        }
        advisory = {
            "ghsa_id": "GHSA-valid",
            "severity": "critical",
            "vulnerabilities": [vulnerability],
        }
        malformed = {
            **advisory,
            "ghsa_id": "GHSA-no-fix",
            "vulnerabilities": [{**vulnerability, "patched_versions": None}],
        }
        fixture.state["security_advisories"] = [malformed, advisory]
        result = plan(fixture.state, fixture.policy, fixture.sources, "2026-10-25T03:00:00Z")
        self.assertEqual(result["electron"]["urgent_reasons"], ["GHSA-valid"])
        self.assertEqual(result["electron"]["action"], "prepare-source")
        self.assertEqual(len(result["clients"]), 1)
        fixture.state["security_advisories"] = [malformed]
        fixture.policy["urgent_versions"] = ["46.0.0-alpha.2"]
        result = plan(fixture.state, fixture.policy, fixture.sources, "2026-10-25T03:00:00Z")
        self.assertEqual(result["electron"]["urgent_reasons"], ["maintainer-override"])
        self.assertEqual(result["electron"]["action"], "prepare-source")


if __name__ == "__main__":
    unittest.main(verbosity=2)
