import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from releases import (
    FORMATS,
    app_release_channel,
    manifest_tag,
    notes,
    previous_release,
    published_releases,
    refresh_notes,
)


def manifest(version="20261026.120000", **changes):
    return {
        "tag_schema": 3,
        "release_key": "b" * 64,
        "version": version,
        "channel": "canary",
        "client_version": "1.0.2374",
        "electron_version": "46.0.0-alpha.1",
        "source_commit": "a" * 40,
        "minimum_glibc": "2.38",
        "workflow_run": "123",
        "assets": {"app" + ext: "c" * 64 for ext in FORMATS},
        "runtime": {"archive_sha256": "d" * 64},
        **changes,
    }


def release(item, **changes):
    return {"tag_name": manifest_tag(item), "draft": False, "prerelease": True, **changes}


class ReleaseNotes(unittest.TestCase):
    def test_approval_button_names_exact_candidate_and_disappears_after_approval(self):
        current = manifest()
        previous = manifest("20261025.120000")
        pending = notes(current, previous)
        self.assertNotIn("aur.archlinux.org", pending)
        self.assertIn(".pkg.tar.zst", pending)
        self.assertIn("[![Approve this release]", pending)
        self.assertIn("/actions/workflows/approve-release.yml)", pending)
        self.assertIn(f"`{manifest_tag(current)}` into **release_tag**", pending)
        self.assertIn("confirm that you tested this build", pending)
        promoted = notes(current, previous, approved=True)
        self.assertEqual(promoted.count("aur.archlinux.org"), 1)
        self.assertIn("**This build has been approved as stable.**", promoted)
        self.assertNotIn("Approve this release", promoted)
        self.assertEqual(notes(current, previous, approved=True), promoted)

    def test_all_tag_generations_are_recognized_without_runtime_caches(self):
        for schema in (1, 2, 3):
            self.assertEqual(
                app_release_channel(manifest_tag(manifest(tag_schema=schema))), "canary"
            )
        for tag in ("runtime-cache-" + "b" * 64, "v1.2.3", "canary-1.0.2374-bad"):
            self.assertIsNone(app_release_channel(tag))

    def test_previous_release_uses_versions_and_channel_track_not_publication_order(self):
        old = manifest("20261025.010203", tag_schema=2)
        previous = manifest("20261026.110000")
        current = manifest()
        future = manifest("20261027.110000")
        stable = manifest("20261026.115900", electron_version="45.0.0")
        other = manifest("20261026.115800", channel="ptb")
        entries = [release(item) for item in (future, old, stable, other, current, previous)]
        entries += [release(manifest("20261026.115959"), draft=True)]
        entries += [{"tag_name": "runtime-cache-fixture", "draft": False}]
        by_tag = {
            manifest_tag(item): item for item in (future, old, stable, other, current, previous)
        }
        self.assertEqual(
            previous_release(current, entries, lambda item: by_tag[item["tag_name"]]), previous
        )
        self.assertIsNone(previous_release(old, entries, lambda item: by_tag[item["tag_name"]]))

    def test_stable_baseline_skips_prereleases_other_tracks_drafts_and_itself_on_retry(self):
        old = manifest("20261024.120000")
        stable = manifest("20261025.120000")
        preview = manifest("20261026.110000")
        current = manifest()
        future = manifest("20261027.120000")
        other = manifest("20261026.115900", channel="ptb")
        other_track = manifest("20261026.115800", electron_version="45.0.0")
        draft = manifest("20261026.115700")
        entries = [
            release(preview),
            release(future, prerelease=False),
            release(old, prerelease=False),
            release(other, prerelease=False),
            release(other_track, prerelease=False),
            release(stable, prerelease=False),
            release(draft, prerelease=False, draft=True),
            {"tag_name": "runtime-cache-fixture", "draft": False},
        ]
        by_tag = {manifest_tag(item): item for item in (old, stable, future, other, other_track)}
        for already_promoted in (False, True):
            # The fetch map omits the candidate and prereleases deliberately:
            # neither should even need a manifest download during approval.
            baseline = previous_release(
                current,
                [*entries, release(current, prerelease=not already_promoted)],
                lambda item: by_tag[item["tag_name"]],
                prerelease=False,
            )
            self.assertEqual(baseline, stable)
        self.assertIsNone(
            previous_release(
                current,
                [release(preview)],
                lambda _: self.fail("Fetched a prerelease"),
                prerelease=False,
            )
        )

    def test_history_paginates_and_filters_drafts_and_non_app_releases(self):
        first, second = release(manifest()), release(manifest("20261025.100000"))
        with patch(
            "releases.api",
            side_effect=[
                [first, {"tag_name": "runtime-cache-fixture", "draft": False}],
                [release(manifest("20261027.100000"), draft=True), second],
                [],
            ],
        ) as api:
            self.assertEqual(published_releases(), [first, second])
            self.assertEqual(api.call_args.args[0], "releases?per_page=100&page=3")

    def test_prerelease_baseline_skips_newer_stable_releases_and_publication_order(self):
        old = manifest("20261023.100000")
        preview = manifest("20261024.100000")
        stable = manifest("20261025.100000")
        current = manifest()
        future = manifest("20261027.100000")
        other = manifest("20261026.115900", channel="ptb")
        other_track = manifest("20261026.115800", electron_version="45.0.0")
        entries = [release(item) for item in (future, old, other, current, other_track, preview)]
        entries += [
            release(stable, prerelease=False),
            release(manifest("20261026.115700"), draft=True),
        ]
        by_tag = {
            manifest_tag(item): item for item in (old, preview, current, future, other, other_track)
        }
        self.assertEqual(
            previous_release(
                current, entries, lambda item: by_tag[item["tag_name"]], prerelease=True
            ),
            preview,
        )
        self.assertIsNone(
            previous_release(old, entries, lambda item: by_tag[item["tag_name"]], prerelease=True)
        )

    def test_client_only_release_explains_changed_inputs_even_with_identical_source(self):
        old = manifest("20261026.110000", client_version="1.0.2369")
        current = manifest(proposal_commit="e" * 40)
        with patch("releases.subprocess.check_output") as log:
            body = notes(current, old)
            log.assert_not_called()
        self.assertIn("1.0.2369 → 1.0.2374", body)
        self.assertIn("Electron **46.0.0-alpha.1** (unchanged)", body)
        self.assertIn("[Update proposal]", body)
        self.assertIn(manifest_tag(old) + "..." + manifest_tag(current), body)

    def test_first_remaining_release_never_dumps_repository_history(self):
        with patch("releases.subprocess.check_output") as log:
            body = notes(manifest(clients_state="f" * 40), None)
            log.assert_not_called()
        self.assertIn("No earlier stable release", body)
        self.assertNotIn("Changes since last pre-release", body)
        self.assertIn("[Client snapshot]", body)

    def test_first_prerelease_after_stable_uses_plain_stable_changes(self):
        current = manifest()
        stable = manifest("20261025.120000", client_version="1.0.2373")
        for preview in (None, manifest("20261024.120000", client_version="1.0.2372")):
            with self.subTest(preview=preview):
                body = notes(current, stable, previous_prerelease=preview)
                self.assertNotIn("<details", body)
                self.assertNotIn("Changes since last pre-release", body)
                self.assertIn("1.0.2373 → 1.0.2374", body)
                self.assertIn(manifest_tag(stable) + "..." + manifest_tag(current), body)
                if preview:
                    self.assertNotIn(manifest_tag(preview) + "...", body)

    def test_followup_prerelease_expands_incremental_changes_and_collapses_stable_rollup(self):
        stable = manifest("20261024.120000")
        preview = manifest("20261025.120000")
        body = notes(manifest(), stable, previous_prerelease=preview)
        self.assertIn("<details>\n<summary>Changes since last stable release</summary>", body)
        self.assertIn("<details open>\n<summary>Changes since last pre-release</summary>", body)
        self.assertEqual(body.count("<details open>"), 1)

    def test_same_version_module_hash_changes_and_electron_rebuild_are_visible(self):
        old = manifest(client={"modules": {"discord_voice": {"full": {"package_sha256": "a"}}}})
        current = copy.deepcopy(old)
        current["client"]["modules"]["discord_voice"]["full"]["package_sha256"] = "b"
        current["runtime"]["archive_sha256"] = "e" * 64
        body = notes(current, old)
        self.assertIn("Discord modules updated: `discord_voice`", body)
        self.assertIn("rebuilt; runtime archive changed", body)

    def test_git_range_includes_all_changes_and_merged_native_work_without_metadata_noise(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def git(*args):
                return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()

            git("init", "--quiet")
            git("config", "user.name", "Fixture")
            git("config", "user.email", "fixture@example.invalid")

            def commit(path, content, message):
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
                git("add", path)
                git("commit", "--quiet", "-m", message)
                return git("rev-parse", "HEAD")

            baseline = commit("scripts/app.py", "old", "Old shipped behavior")
            # Candidates and stable releases both retain the full history.
            for index in range(35):
                sha = commit("scripts/app.py", str(index), f"Fix behavior {index}")
                if index == 29:
                    preview = manifest("20261026.110000", source_commit=sha)
            git("checkout", "--quiet", "-b", "native-change")
            metadata = root / "config/release-notes.json"
            metadata.parent.mkdir(parents=True, exist_ok=True)
            metadata.write_text('{ "schema": 1, "commits": {} }\n')
            git("add", "config/release-notes.json")
            commit("native/bridge/src/lib.rs", "fixture", "Fix native-only merged behavior")
            commit("Cargo.toml", "fixture", "Update Rust dependency policy")
            git("checkout", "--quiet", "-")
            git("merge", "--quiet", "--no-ff", "native-change", "-m", "Merge native work")
            commit("docs/install.md", "docs", "Documentation only")
            commit(
                "config/release-notes.json", '{"schema":1,"commits":{}}', "Describe release changes"
            )
            commit("config/update-policy.json", "{}", "Approve tested Canary release")
            head = commit(
                "config/update-proposals/canary-prerelease.json", "{}", "Propose Discord Canary"
            )
            with patch("releases.ROOT", root):
                body = notes(
                    manifest(source_commit=head),
                    manifest("20261025.100000", source_commit=baseline),
                    previous_prerelease=preview,
                )
                stable_body = notes(
                    manifest(source_commit=head),
                    manifest("20261025.100000", source_commit=baseline),
                    approved=True,
                )
                first_stable = notes(manifest(source_commit=head), None, approved=True)
        stable_changes, preview_changes = body.split(
            "<summary>Changes since last pre-release</summary>"
        )
        self.assertEqual(stable_changes.count("- Fix behavior"), 35)
        self.assertEqual(preview_changes.count("- Fix behavior"), 5)
        self.assertIn(
            manifest_tag(preview) + "..." + manifest_tag(manifest(source_commit=head)),
            preview_changes,
        )
        self.assertNotIn("Fix behavior 29", preview_changes)
        self.assertEqual(stable_body.count("- Fix behavior"), 35)
        self.assertEqual(first_stable.count("- Fix behavior"), 35)
        self.assertIn("Old shipped behavior", first_stable)
        self.assertIn("First stable release", first_stable)
        self.assertNotIn("Additional project changes", stable_body)
        for changelog in (body, stable_body, first_stable):
            self.assertIn("Fix native-only merged behavior", changelog)
            self.assertIn("Update Rust dependency policy", changelog)
            self.assertNotIn("Merge native work", changelog)
            self.assertNotIn("Additional project changes", changelog)
        for excluded in (
            "Old shipped behavior",
            "Documentation only",
            "Describe release changes",
            "Approve tested",
            "Propose Discord",
        ):
            self.assertNotIn(excluded, body)
            self.assertNotIn(excluded, stable_body)

    def test_categories_reserve_new_for_explicit_features_and_escape_subjects(self):
        commits = [
            "feat(mods): Choose a client mod",
            "fix: Restore saved choices",
            "Add internal test coverage",
            "perf(audio): Reduce refresh work",
            "feat!: Show [choices] @everyone",
        ]
        with patch(
            "releases.subprocess.check_output",
            return_value="\n".join("e" * 40 + "\t" + title for title in commits),
        ):
            body = notes(manifest(source_commit="b" * 40), manifest())
        self.assertIn("### Discord and Electron", body)
        self.assertIn("### Fixes", body)
        self.assertIn("### Improvements", body)
        self.assertEqual(body.count("**NEW**"), 2)
        self.assertIn("**NEW** Choose a client mod", body)
        self.assertIn("- Add internal test coverage", body)
        self.assertNotIn("@everyone", body)
        self.assertIn(r"\[choices\]", body)

    def test_broad_merged_commits_expand_all_details_only_in_the_shipped_range(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def git(*args):
                return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()

            git("init", "--quiet")
            git("config", "user.name", "Fixture")
            git("config", "user.email", "fixture@example.invalid")

            def commit(text, message):
                target = root / "integration/desktop.cjs"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text)
                git("add", ".")
                git("commit", "--quiet", "-m", message)
                return git("rev-parse", "HEAD")

            baseline = commit("old", "Previous stable")
            git("checkout", "--quiet", "-b", "desktop")
            broad = commit("new", "fix: preserve desktop integration")
            git("checkout", "--quiet", "-")
            git("merge", "--quiet", "--no-ff", "desktop", "-m", "Merge desktop work")
            shipped = git("rev-parse", "HEAD")
            future = commit("future", "feat: Future work")
            data = {
                "schema": 1,
                "commits": {
                    baseline: {"New": ["Already shipped"]},
                    broad: {
                        "New": ["Choose a PiP corner"],
                        "Fixes": ["Open web links externally", "Keep [choices] @everyone safe"],
                    },
                    future: {"New": ["Not yet shipped"]},
                },
            }
            path = root / "config/release-notes.json"
            path.parent.mkdir()
            path.write_text(json.dumps(data))
            current = manifest(source_commit=shipped)
            previous = manifest("20261025.100000", source_commit=baseline)
            with patch("releases.ROOT", root):
                candidate = notes(current, previous)
                approved = notes(current, previous, approved=True)
                self.assertEqual(notes(current, previous, approved=True), approved)
                # A client-only follow-up must not repeat code that already shipped.
                followup = notes(manifest(source_commit=shipped), current)

        for body in (candidate, approved):
            comparison = body.split("<summary>Changes since last stable release</summary>\n\n")[-1]
            summary, inventory = comparison.split("<details>", 1)
            self.assertIn("**NEW** Choose a PiP corner", summary)
            self.assertIn("Open web links externally", summary)
            self.assertIn(r"Keep \[choices\] ＠everyone safe", summary)
            self.assertEqual(summary.count("/commit/" + broad), 3)
            self.assertNotIn("preserve desktop integration", summary)
            self.assertIn("fix: preserve desktop integration", inventory)
            self.assertIn("/commit/" + broad, inventory)
            self.assertNotIn("Already shipped", body)
            self.assertNotIn("Not yet shipped", body)
            self.assertNotIn("Future work", body)
            self.assertIn(manifest_tag(previous) + "..." + manifest_tag(current), body)
        self.assertNotIn("Choose a PiP corner", followup)

    def test_invalid_detailed_notes_fail_instead_of_silently_omitting_changes(self):
        for data in (
            [],
            {"schema": 2, "commits": {}},
            {"schema": 1, "commits": []},
            {"schema": 1, "commits": {"abc1234": {"New": ["A change"]}}},
            *[
                {"schema": 1, "commits": {"a" * 40: groups}}
                for groups in (
                    {},
                    {"Unknown": ["A change"]},
                    {"Fixes": "A change"},
                    {"New": []},
                    {"New": [""]},
                    {"New": [False]},
                    {"New": ["First line\nSecond line"]},
                )
            ],
        ):
            with self.subTest(data=data), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                path = root / "config/release-notes.json"
                path.parent.mkdir()
                path.write_text(json.dumps(data))
                with patch("releases.ROOT", root), self.assertRaises(ValueError):
                    notes(manifest(), manifest())

    def test_refresh_only_patches_bodies_and_preserves_each_release_approval_state(self):
        old, preview, new = (manifest("20261025.100000"), manifest("20261026.110000"), manifest())
        latest = manifest("20261027.120000")
        followup = manifest("20261028.120000")
        entries = [
            release(old, id=1, prerelease=False, body="old notes"),
            release(preview, id=2, body="preview notes"),
            release(new, id=3, prerelease=False, body="new notes"),
            release(latest, id=4, body="latest preview"),
            release(followup, id=5, body="followup preview"),
        ]
        by_tag = {manifest_tag(item): item for item in (old, preview, new, latest, followup)}
        writes = []

        def api(endpoint, **kwargs):
            if kwargs.get("method") == "PATCH":
                writes.append((endpoint, kwargs["data"]))
                entries[int(endpoint.rsplit("/", 1)[-1]) - 1].update(kwargs["data"])
            else:
                return entries[int(endpoint.rsplit("/", 1)[-1]) - 1]

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("releases.ROOT", Path(temporary)),
            patch("releases.published_releases", return_value=entries),
            patch("releases.published_manifest", side_effect=lambda item: by_tag[item["tag_name"]]),
            patch("releases.api", side_effect=api),
            patch("releases.subprocess.check_output", return_value=""),
            patch("builtins.print") as status,
        ):
            refresh_notes(None)
            self.assertFalse(writes)
            refresh_notes(None, apply=True)
            count = len(writes)
            status.reset_mock()
            refresh_notes(None, apply=True)
            self.assertEqual(len(writes), count, "Applying unchanged descriptions is idempotent")
            self.assertEqual(len(status.call_args_list), len(entries))
            self.assertTrue(
                all(call.args[0].startswith("Unchanged notes: ") for call in status.call_args_list)
            )
        self.assertEqual(len(writes), 5)
        self.assertTrue(all(set(data) == {"body"} for _, data in writes))
        self.assertIn("approved as stable", writes[0][1]["body"])
        self.assertIn("hasn't been approved", writes[1][1]["body"])
        self.assertIn("aur.archlinux.org", writes[0][1]["body"])
        self.assertNotIn("aur.archlinux.org", writes[1][1]["body"])
        stable_body = writes[2][1]["body"]
        self.assertIn(manifest_tag(old) + "..." + manifest_tag(new), stable_body)
        self.assertNotIn(manifest_tag(preview) + "...", stable_body)
        latest_body = writes[3][1]["body"]
        self.assertNotIn("<details", latest_body)
        self.assertIn(manifest_tag(new) + "..." + manifest_tag(latest), latest_body)
        self.assertNotIn(manifest_tag(preview) + "..." + manifest_tag(latest), latest_body)
        followup_body = writes[4][1]["body"]
        self.assertIn(
            "<details>\n<summary>Changes since last stable release</summary>", followup_body
        )
        self.assertIn(
            "<details open>\n<summary>Changes since last pre-release</summary>", followup_body
        )
        self.assertIn(manifest_tag(latest) + "..." + manifest_tag(followup), followup_body)
        self.assertIn("[Compare with previous pre-release]", followup_body)
