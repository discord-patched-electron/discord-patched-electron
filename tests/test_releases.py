"""Approval never replaces packages, accepts failed checks or rolls releases back."""

import copy
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import ROOT, read_json, write_json
from releases import (
    FORMATS,
    PROJECT,
    install_notes,
    notes,
    prepare,
    release_tag,
    validate_approval,
    verify_assets,
)
from schedule_updates import client_result


class ReleaseApproval(unittest.TestCase):
    def setUp(self):
        self.manifest = {
            "version": "20261024.120000",
            "client_version": "1.0.2365",
            "channel": "canary",
            "electron_version": "46.0.0-alpha.1",
            "source_commit": "a" * 40,
            "source_dirty": False,
            "release_key": "b" * 64,
        }
        self.release = {"draft": False, "tag_name": release_tag("1.0.2365", "b" * 64)}
        self.workflow = {
            "conclusion": "success",
            "status": "completed",
            "head_branch": "main",
            "head_repository": {"full_name": PROJECT},
            "head_sha": "a" * 40,
            "path": ".github/workflows/release.yml",
        }

    def test_rejects_failed_foreign_or_mismatched_release_evidence(self):
        validate_approval(self.release, self.manifest, self.workflow, None)
        for key, value in (
            ("conclusion", "failure"),
            ("status", "in_progress"),
            ("head_branch", "another"),
            ("head_sha", "c" * 40),
            ("head_repository", {"full_name": "elsewhere/project"}),
            ("path", ".github/workflows/build.yml"),
        ):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_approval(self.release, self.manifest, {**self.workflow, key: value}, None)
        with self.assertRaises(ValueError):
            validate_approval({**self.release, "draft": True}, self.manifest, self.workflow, None)
        with self.assertRaises(ValueError):
            validate_approval(
                {**self.release, "tag_name": "wrong"}, self.manifest, self.workflow, None
            )

    def test_same_release_can_resume_approval_but_older_candidate_cannot_replace_newer(self):
        approved = {"tag": self.release["tag_name"], "version": self.manifest["version"]}
        validate_approval(self.release, self.manifest, self.workflow, approved)
        approved.update(tag="different", version="20261025.010000")
        with self.assertRaisesRegex(ValueError, "rollback"):
            validate_approval(self.release, self.manifest, self.workflow, approved)

    def test_pacman_artifact_is_required_and_every_asset_is_verified(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            assets = {}
            for ext in FORMATS:
                name = "fixture" + ext
                (directory / name).write_bytes(b"tested bytes")
                assets[name] = hashlib.sha256(b"tested bytes").hexdigest()
            manifest = {**self.manifest, "assets": assets}
            verify_assets(directory, manifest)
            with self.assertRaisesRegex(ValueError, "four"):
                verify_assets(
                    directory,
                    {
                        **manifest,
                        "assets": {
                            k: v for k, v in assets.items() if not k.endswith(".pkg.tar.zst")
                        },
                    },
                )
            with self.assertRaisesRegex(ValueError, "clean builds"):
                verify_assets(directory, {**manifest, "source_dirty": True})
            with self.assertRaisesRegex(ValueError, "Disabled Discord"):
                verify_assets(directory, {**manifest, "channel": "ptb"})
            with self.assertRaisesRegex(ValueError, "Disabled Electron"):
                verify_assets(directory, {**manifest, "electron_version": "45.0.0"})
            (directory / "fixture.pkg.tar.zst").write_bytes(b"replacement")
            with self.assertRaisesRegex(ValueError, "checksum"):
                verify_assets(directory, manifest)

    def test_identical_public_candidate_skips_packaging_and_retains_immutable_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            client = read_json(ROOT / "config/clients.lock.json")["clients"]["canary"]

            def snapshot(commit, output):
                self.assertEqual(commit, "d" * 40)
                write_json(output, {"schema": 1, "clients": {"canary": client}})

            with (
                patch("releases.ROOT", directory),
                patch("releases.fetch_clients", side_effect=snapshot),
                patch("releases.fetch_runtime", return_value={"source": {"target": "prerelease"}}),
                patch(
                    "releases.api",
                    return_value={"draft": False, "created_at": "2026-10-24T12:00:00Z"},
                ) as api,
                patch.dict(
                    os.environ,
                    {
                        "CLIENTS_STATE": "d" * 40,
                        "RUNTIME": "123",
                        "GITHUB_OUTPUT": str(directory / "outputs"),
                        "GITHUB_STEP_SUMMARY": str(directory / "summary"),
                    },
                ),
            ):
                prepare()
            self.assertIn("needed=false", (directory / "outputs").read_text())
            self.assertEqual(api.call_count, 1)

    def test_release_table_links_to_exact_assets_including_older_versioned_names(self):
        for prefix in ("app-x86_64", "app-20261024.120000-x86_64"):
            manifest = {**self.manifest, "assets": {prefix + ext: "c" * 64 for ext in FORMATS}}
            body = install_notes(manifest)
            self.assertIn("| Distribution | Package | AUR/Repo |", body)
            self.assertIn(
                "https://aur.archlinux.org/packages/discord-canary-prerelease-patched-electron-bin",
                body,
            )
            for ext in FORMATS:
                self.assertIn(f"/{self.release['tag_name']}/{prefix}{ext})", body)
            self.assertIn("/SHA256SUMS)", body)
            self.assertNotIn(";", body)

    def test_notes_put_results_beside_changelog_and_use_neutral_approval_copy(self):
        manifest = {
            **self.manifest,
            "assets": {"app" + ext: "c" * 64 for ext in FORMATS},
            "minimum_glibc": "2.38",
            "workflow_run": "123",
        }
        with patch("releases.subprocess.check_output", return_value=""):
            body = notes(manifest, None)
        self.assertIn("**This build hasn't been approved as stable yet.**", body)
        self.assertIn(
            f"[Full changelog](https://github.com/{PROJECT}/commits/"
            + "a" * 40
            + ") · [✅ Test results]",
            body,
        )
        self.assertNotIn("All checks passed", body)

    def test_disabled_release_channel_stops_before_network_requests(self):
        with patch.dict(os.environ, {"CHANNEL": "ptb"}), patch("releases.api") as api:
            with self.assertRaisesRegex(ValueError, "Disabled Discord"):
                prepare()
            api.assert_not_called()

    def test_existing_candidate_is_success_but_failed_publish_is_not(self):
        jobs = [
            {
                "name": "clients (canary) / " + suffix,
                "status": "completed",
                "conclusion": "success" if suffix == "inputs" else "skipped",
            }
            for suffix in (
                "inputs",
                "package",
                "publish",
                "native / validate",
                "bundled / validate",
                "audio / validate",
            )
        ]
        self.assertEqual(client_result("canary", jobs, True), "success")
        actual_skipped_jobs = copy.deepcopy(jobs)
        for job in actual_skipped_jobs:
            job["name"] = job["name"].removesuffix(" / validate")
        self.assertEqual(client_result("canary", actual_skipped_jobs, True), "success")
        failed = copy.deepcopy(jobs)
        for job in failed:
            job["conclusion"] = "success"
        failed[2]["conclusion"] = "failure"
        self.assertEqual(client_result("canary", failed, True), "failure")
        self.assertEqual(client_result("canary", failed[:-1], True), "failure")
