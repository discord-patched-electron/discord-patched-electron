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
    existing_candidate,
    install_notes,
    manifest_tag,
    notes,
    prepare,
    release_tag,
    reserve_tag,
    validate_approval,
    verify_assets,
)
from schedule_updates import client_result


class ReleaseApproval(unittest.TestCase):
    def test_date_tags_sort_by_version_and_keep_legacy_manifests_valid(self):
        self.assertEqual(manifest_tag(self.manifest), self.release["tag_name"])
        manifest = {**self.manifest, "tag_schema": 2, "version": "20261025.010203"}
        tag = manifest_tag(manifest)
        self.assertEqual(tag, "v20261025.10203.0-canary." + "b" * 16)
        validate_approval({**self.release, "tag_name": tag}, manifest, self.workflow, None)
        body = install_notes({**manifest, "assets": {"app" + ext: "c" * 64 for ext in FORMATS}})
        self.assertIn("/download/" + tag + "/", body)
        with self.assertRaises(ValueError):
            release_tag("1.0.2365", "b" * 64, version="invalid")

    def test_date_tag_deduplication_searches_later_pages_and_drafts(self):
        candidate = {"tag_name": "v20261025.10203.0-canary." + "b" * 16, "draft": True}
        with patch(
            "releases.api", side_effect=[None, [{"tag_name": "unrelated"}], [candidate]]
        ) as api:
            self.assertEqual(existing_candidate("1.0.2365", "b" * 64, "canary"), candidate)
            self.assertEqual(api.call_args.args[0], "releases?per_page=100&page=2")
        with patch("releases.api", side_effect=[None, []]):
            self.assertIsNone(existing_candidate("1.0.2365", "b" * 64, "canary"))

    def test_tag_reservation_is_idempotent_and_never_moves_existing_tags(self):
        with patch("releases.api", return_value=None) as api:
            reserve_tag("candidate", "a" * 40)
            self.assertEqual(
                api.call_args.kwargs["data"], {"ref": "refs/tags/candidate", "sha": "a" * 40}
            )
        with patch("releases.api", return_value={"object": {"sha": "a" * 40}}) as api:
            reserve_tag("candidate", "a" * 40)
            self.assertEqual(api.call_count, 1)
            with self.assertRaisesRegex(ValueError, "another commit"):
                reserve_tag("candidate", "b" * 40)

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

    def test_prepare_retries_reuse_identity_and_only_reserve_unpublished_tags(self):
        client = read_json(ROOT / "config/clients.lock.json")["clients"]["canary"]
        key = "b" * 64
        dated = release_tag(client["version"], key, version="20261024.130203")
        legacy = release_tag(client["version"], key)
        for kind in ("new", "draft", "published", "legacy"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                existing = {
                    "draft": kind == "draft",
                    # GitHub's release creation timestamp can be the commit time,
                    # which differs from the build version encoded in a date tag.
                    "created_at": "2026-10-24T12:00:00Z",
                    "tag_name": legacy if kind == "legacy" else dated,
                }

                def api(endpoint, kind=kind, existing=existing, **kwargs):
                    if endpoint == "releases/tags/" + legacy:
                        return existing if kind == "legacy" else None
                    if endpoint == "releases?per_page=100&page=1":
                        return [] if kind == "new" else [existing]
                    if endpoint == "actions/runs/123":
                        return {"created_at": "2026-10-24T13:02:03Z"}
                    self.fail("Unexpected API call: " + endpoint)

                def snapshot(commit, output):
                    self.assertEqual(commit, "d" * 40)
                    write_json(output, {"schema": 1, "clients": {"canary": client}})

                with (
                    patch("releases.ROOT", directory),
                    patch("releases.fetch_clients", side_effect=snapshot),
                    patch(
                        "releases.fetch_runtime", return_value={"source": {"target": "prerelease"}}
                    ),
                    patch("releases.integration_key", return_value=key),
                    patch("releases.api", side_effect=api),
                    patch("releases.reserve_tag") as reserve,
                    patch.dict(
                        os.environ,
                        {
                            "CHANNEL": "canary",
                            "CLIENTS_STATE": "d" * 40,
                            "RUNTIME": "123",
                            "GITHUB_RUN_ID": "123",
                            "GITHUB_SHA": "a" * 40,
                            "PUBLISH": "true",
                            "GITHUB_OUTPUT": str(directory / "outputs"),
                            "GITHUB_STEP_SUMMARY": str(directory / "summary"),
                        },
                    ),
                ):
                    prepare()
                output = dict(
                    line.split("=", 1) for line in (directory / "outputs").read_text().splitlines()
                )
                self.assertEqual(output["tag"], existing["tag_name"])
                if kind != "legacy":
                    self.assertEqual(output["version"], "20261024.130203")
                    self.assertEqual(output["epoch"], "1792846923")
                if kind in ("new", "draft"):
                    self.assertEqual(output["needed"], "true")
                    reserve.assert_called_once_with(dated, "a" * 40)
                else:
                    self.assertEqual(output["needed"], "false")
                    reserve.assert_not_called()

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
