"""Approval never replaces packages, accepts failed checks or rolls releases back."""

import copy
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import ROOT, digest, read_json, sha256, write_json
from releases import (
    FORMATS,
    PROJECT,
    approve,
    candidate_time,
    existing_candidate,
    install_notes,
    manifest_tag,
    notes,
    prepare,
    publish,
    release_tag,
    reserve_tag,
    validate_approval,
    verify_assets,
)
from schedule_updates import client_result


class ReleaseApproval(unittest.TestCase):
    def test_candidate_clock_is_monotonic_and_pinned_to_checkout(self):
        with tempfile.TemporaryDirectory() as temporary, patch("releases.ROOT", Path(temporary)):
            path = Path(temporary) / "config/update-proposals/canary-prerelease.json"
            stamp = "2026-11-01T09:15:40Z"

            def version(channel="canary", electron="46.0.0-alpha.1"):
                return candidate_time(stamp, channel, electron).strftime("%Y%m%d.%H%M%S")

            self.assertEqual(version(), "20261101.091540")
            write_json(path, {"version": "20261101.094751"})
            self.assertEqual(version(), "20261101.094752")
            self.assertEqual(version(), "20261101.094752")  # Retry has the same identity.
            self.assertEqual(version("stable"), "20261101.091540")
            self.assertEqual(version(electron="45.0.0"), "20261101.091540")
            write_json(path, {"version": "20261101.091539"})
            self.assertEqual(version(), "20261101.091540")
            write_json(path, {"version": "20261031.235959"})
            self.assertEqual(version(), "20261101.091540")

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

    def test_fixed_width_tags_sort_chronologically(self):
        versions = [
            "20261025.010203",
            "20261025.095959",
            "20261025.100000",
            "20261026.000000",
            "20270101.000000",
        ]
        tags = [release_tag("1.0.2365", "b" * 64, version=v) for v in versions]
        self.assertEqual(tags, sorted(tags))
        self.assertEqual(
            tags, sorted(tags, key=lambda t: tuple(map(int, t[1:].split("-")[0].split("."))))
        )
        self.assertEqual(tags[0], "v20261025010203.0.0-canary." + "b" * 16)
        manifest = {**self.manifest, "tag_schema": 3, "version": versions[0]}
        self.assertEqual(manifest_tag(manifest), tags[0])
        validate_approval({**self.release, "tag_name": tags[0]}, manifest, self.workflow, None)
        with patch("releases.api", side_effect=[None, [{"tag_name": tags[0]}]]):
            self.assertEqual(
                existing_candidate("1.0.2365", "b" * 64, "canary")["tag_name"], tags[0]
            )
        for version in ("20260230.120000", "20261025.250000"):
            with self.assertRaises(ValueError):
                release_tag("1.0.2365", "b" * 64, version=version)

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

    def test_publication_requires_the_reserved_source_without_retargeting(self):
        for tag_state in ("missing", "wrong_commit", "annotated", "valid"):
            with self.subTest(tag_state=tag_state), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary) / "packages"
                manifest = {**self.manifest, "assets": {"app.pkg.tar.zst": "c" * 64}}
                write_json(directory / "release.json", manifest)
                tag = manifest_tag(manifest)
                tagged = {
                    "object": {
                        "type": "tag" if tag_state == "annotated" else "commit",
                        "sha": "c" * 40 if tag_state == "wrong_commit" else "a" * 40,
                    }
                }

                def api(endpoint, tag=tag, tag_state=tag_state, tagged=tagged, **kwargs):
                    if endpoint == "git/ref/tags/" + tag:
                        return None if tag_state == "missing" else tagged
                    if endpoint == "releases/tags/" + tag:
                        return None
                    self.fail("Unexpected API call: " + endpoint)

                with (
                    patch("releases.api", side_effect=api),
                    patch("releases.verify_assets"),
                    patch("releases.verify_signatures"),
                    patch("releases.cache_runtime") as cache,
                    patch("releases.published_releases", return_value=[]),
                    patch("releases.notes", return_value="Tested release"),
                    patch("releases.run") as run,
                    patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(directory / "summary")}),
                ):
                    if tag_state != "valid":
                        with self.assertRaisesRegex(ValueError, "qualified source commit"):
                            publish(directory)
                        cache.assert_not_called()
                        run.assert_not_called()
                    else:
                        publish(directory)
                        create = run.call_args_list[0].args
                        self.assertEqual(create[:4], ("gh", "release", "create", tag))
                        self.assertIn("--verify-tag", create)
                        self.assertNotIn("--target", create)
                        self.assertEqual(run.call_args_list[-1].args[:3], ("gh", "release", "edit"))

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

    def test_approval_generates_rollup_before_mutation_and_only_publishes_complete_notes(self):
        for fail_notes in (False, True):
            with self.subTest(fail_notes=fail_notes), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                capacity = {"synthetic": True}
                runtime = {
                    "version": "46.0.0-alpha.1",
                    "cache_release": "runtime-cache-fixture",
                    "archive_sha256": "d" * 64,
                    "capacity_sha256": digest(capacity),
                    "run": "42",
                    "compiler_commit": "e" * 40,
                }
                current = {
                    **self.manifest,
                    "tag_schema": 3,
                    "workflow_run": "23",
                    "minimum_glibc": "2.38",
                    "assets": {
                        "app" + ext: hashlib.sha256(b"fixture").hexdigest() for ext in FORMATS
                    },
                    "runtime": runtime,
                }
                baseline = {**current, "version": "20261023.120000", "source_commit": "c" * 40}
                preview = {**current, "version": "20261024.110000", "source_commit": "f" * 40}
                tag = manifest_tag(current)
                directory = root / "out/approve-release" / tag
                write_json(directory / "release.json", current)
                for name in current["assets"]:
                    (directory / name).write_bytes(b"fixture")
                (directory / "SHA256SUMS").write_text("fixture")
                write_json(directory / "runtime/capacity.json", capacity)
                policy_path = root / "config/update-policy.json"
                write_json(policy_path, {"last_good_runtime": runtime})
                policy_before = policy_path.read_bytes()
                approved_path = root / "config/approved-release.json"
                release = {
                    "id": 7,
                    "tag_name": tag,
                    "draft": False,
                    "prerelease": True,
                    "body": "Short candidate notes",
                    "assets": [
                        {"name": name, "digest": "sha256:" + sha256(directory / name)}
                        for name in (*current["assets"], "release.json", "SHA256SUMS")
                    ],
                }
                entries = [
                    release,
                    *[
                        {"tag_name": manifest_tag(item), "draft": False, "prerelease": pending}
                        for item, pending in ((preview, True), (baseline, False))
                    ],
                ]
                writes = []

                def api(
                    endpoint, writes=writes, tag=tag, release=release, current=current, **kwargs
                ):
                    if kwargs.get("method") == "PATCH":
                        writes.append((endpoint, kwargs["data"]))
                        return {}
                    return {
                        "releases/tags/" + tag: release,
                        "actions/runs/23": self.workflow,
                        "commits/" + tag: {"sha": current["source_commit"]},
                        "releases/tags/runtime-cache-fixture": {
                            "draft": False,
                            "assets": [
                                {"name": "runtime-dist.zip", "digest": "sha256:" + "d" * 64}
                            ],
                        },
                        "actions/runs/42": {
                            "conclusion": "success",
                            "head_sha": "e" * 40,
                            "updated_at": "fixture",
                        },
                    }[endpoint]

                with (
                    patch("releases.ROOT", root),
                    patch("releases.api", side_effect=api),
                    patch("releases.run"),
                    patch("releases.verify_assets"),
                    patch("releases.verify_signatures"),
                    patch("releases.signature_files", return_value=[]),
                    patch("releases.published_releases", return_value=entries),
                    patch("releases.published_manifest", return_value=baseline) as fetch,
                    patch(
                        "releases.subprocess.run", return_value=subprocess.CompletedProcess([], 0)
                    ),
                    patch(
                        "releases.subprocess.check_output",
                        side_effect=RuntimeError("Missing changelog history")
                        if fail_notes
                        else None,
                        return_value="a" * 40 + "\tfeat: Change from an earlier prerelease\n",
                    ) as log,
                    patch.dict(
                        os.environ,
                        {
                            "GITHUB_ACTOR": "fixture",
                            "GITHUB_STEP_SUMMARY": str(root / "summary"),
                        },
                    ),
                ):
                    if fail_notes:
                        with self.assertRaisesRegex(RuntimeError, "Missing changelog"):
                            approve(tag)
                    else:
                        approve(tag)
                    fetch.assert_called_once_with(entries[-1])
                    self.assertIn(
                        baseline["source_commit"] + ".." + current["source_commit"],
                        log.call_args.args[0],
                    )
                if fail_notes:
                    self.assertEqual(writes, [])
                    self.assertEqual(policy_path.read_bytes(), policy_before)
                    self.assertFalse(approved_path.exists())
                else:
                    self.assertEqual(len(writes), 1)
                    endpoint, data = writes[0]
                    self.assertEqual(endpoint, "releases/7")
                    self.assertEqual(set(data), {"prerelease", "make_latest", "body"})
                    self.assertFalse(data["prerelease"])
                    self.assertEqual(data["make_latest"], "true")
                    self.assertIn("**NEW** Change from an earlier prerelease", data["body"])
                    self.assertIn(manifest_tag(baseline) + "..." + tag, data["body"])
                    self.assertNotIn("Short candidate notes", data["body"])

    def test_optional_appimage_is_verified_and_linked_without_breaking_old_releases(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            assets = {}
            for ext in (*FORMATS, ".AppImage"):
                name = "fixture" + ext
                (directory / name).write_bytes(b"tested bytes")
                assets[name] = hashlib.sha256(b"tested bytes").hexdigest()
            manifest = {**self.manifest, "assets": assets}
            verify_assets(directory, manifest)
            self.assertIn("fixture.AppImage", install_notes(manifest))
            duplicate = {**assets, "duplicate.AppImage": assets["fixture.AppImage"]}
            with self.assertRaisesRegex(ValueError, "at most one"):
                verify_assets(directory, {**manifest, "assets": duplicate})
            (directory / "fixture.AppImage").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "checksum"):
                verify_assets(directory, manifest)

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

    def test_application_asset_is_required_and_hash_bound_to_the_signed_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            assets = {}
            for name in [*("app" + ext for ext in FORMATS), "application.zip"]:
                (directory / name).write_bytes(b"tested bytes")
                assets[name] = hashlib.sha256(b"tested bytes").hexdigest()
            application = {
                "bootstrap_api": 1,
                "asset": "application.zip",
                "version": self.manifest["version"],
                "archive_sha256": assets["application.zip"],
                "index_sha256": "f" * 64,
            }
            manifest = {**self.manifest, "assets": assets, "application": application}
            verify_assets(directory, manifest)
            for change in (
                {"asset": "other.zip"},
                {"archive_sha256": "a" * 64},
                {"bootstrap_api": 2},
            ):
                with self.subTest(change=change), self.assertRaises(ValueError):
                    verify_assets(directory, {**manifest, "application": {**application, **change}})
            (directory / "application.zip").write_bytes(b"corrupt bytes")
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
                        "releases.fetch_runtime",
                        return_value={
                            "source": {"target": "prerelease"},
                            "compiled_version": "46.0.0-alpha.1",
                            "runtime_key": "e" * 64,
                            "archive_sha256": "f" * 64,
                        },
                    ),
                    patch("releases.integration_key", return_value=key),
                    patch("releases.api", side_effect=api),
                    patch("releases.reserve_tag") as reserve,
                    patch("releases.record_proposal", return_value="9" * 40) as proposal,
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
                    self.assertEqual(output["proposal_commit"], "9" * 40)
                    self.assertEqual(proposal.call_args.args[0]["client"], client)
                else:
                    self.assertEqual(output["needed"], "false")
                    reserve.assert_not_called()
                    proposal.assert_not_called()

    def test_release_table_links_to_exact_assets_including_older_versioned_names(self):
        for prefix in ("app-x86_64", "app-20261024.120000-x86_64"):
            manifest = {**self.manifest, "assets": {prefix + ext: "c" * 64 for ext in FORMATS}}
            body = install_notes(manifest, approved=True)
            self.assertIn("| Distribution | Package |", body)
            self.assertIn(
                "| Arch Linux | [AUR](https://aur.archlinux.org/packages/"
                "discord-canary-prerelease-patched-electron-bin) |",
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
