import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ci_scope import SCOPES, changed_paths, event_scope, select


def enabled(result):
    return {name for name, value in result["checks"].items() if value}


class CheckSelection(unittest.TestCase):
    def test_focused_changes_select_their_consumers(self):
        cases = {
            "README.md": set(),
            "docs/agent/releases.md": set(),
            "scripts/releases.py": {"unit"},
            "scripts/release_proposals.py": {"unit"},
            ".github/workflows/approve-release.yml": {"unit"},
            "tests/test_releases.py": {"unit"},
            "tests/audio/adapter.test.cjs": {"unit"},
            "integration/updater/launch-settings.css": {"unit", "packaging"},
            "integration/updater/openasar/config.html": {"unit", "packaging"},
            "integration/launcher/discord-launcher.sh": {"unit", "packaging", "launcher"},
            "scripts/patch_app_audio.py": {"unit", "packaging", "audio"},
            "scripts/desktop_identity.py": {"unit", "packaging", "audio"},
            "scripts/patch_krisp.py": {"unit", "packaging", "native"},
            "tests/audio/speech.wav": {"unit", "native", "audio"},
            "tests/wayland/main.cjs": {"unit", "audio"},
            "tests/runtime/frame_addon.cc": {"unit", "native"},
            "ci/arch/Dockerfile": {"unit", "packaging", "audio"},
            "ci/audio/niri.Dockerfile": {"unit", "audio"},
            "integration/camera/v4l2-probe.c": {"unit", "packaging", "camera"},
            "scripts/compiler_cache.py": {"unit", "compiler"},
            "LICENSE": {"unit", "packaging"},
            ".github/workflows/app-audio.yml": {"unit", "audio"},
            ".github/workflows/native-runtime.yml": {"unit", "native"},
            ".github/workflows/package-check.yml": {"unit", "packaging"},
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(enabled(select([path])), expected)

    def test_shared_unknown_and_selector_inputs_keep_full_coverage(self):
        for path in (
            "scripts/common.py",
            "scripts/channels.py",
            "scripts/client_inputs.py",
            "config/clients.lock.json",
            "config/sources.lock.json",
            "config/build.json",
            "patches/future.patch",
            "scripts/new-helper.py",
            "integration/new-adapter/file.c",
            "pyproject.toml",
            "uv.lock",
            "bun.lock",
            "package.json",
            "scripts/ci_scope.py",
            ".github/workflows/check.yml",
        ):
            with self.subTest(path=path):
                self.assertEqual(enabled(select([path])), set(SCOPES))

    def test_mixed_changes_union_scopes(self):
        self.assertEqual(
            enabled(select(["README.md", "scripts/patch_app_audio.py", "scripts/patch_krisp.py"])),
            {"unit", "packaging", "native", "audio"},
        )

    def test_manual_unknown_and_malformed_events_run_all(self):
        for event_name, event in (
            ("workflow_dispatch", {}),
            ("future_event", {}),
            ("push", {}),
            ("pull_request", {"pull_request": None}),
            ("push", {"before": "--help", "after": "f" * 40}),
        ):
            with self.subTest(event=event_name, payload=event):
                self.assertEqual(enabled(event_scope(Path("."), event_name, event)), set(SCOPES))

    def test_cli_writes_boolean_outputs_and_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            event = root / "event.json"
            event.write_text("{}")
            output, summary = root / "output", root / "summary"
            result = subprocess.run(
                [sys.executable, str(Path(__file__).resolve().parents[1] / "scripts/ci_scope.py")],
                env={
                    **os.environ,
                    "GITHUB_EVENT_NAME": "workflow_dispatch",
                    "GITHUB_EVENT_PATH": str(event),
                    "GITHUB_OUTPUT": str(output),
                    "GITHUB_STEP_SUMMARY": str(summary),
                },
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(enabled(json.loads(result.stdout)), set(SCOPES))
            self.assertEqual(set(output.read_text().splitlines()), {f"{s}=true" for s in SCOPES})
            self.assertIn("Manual run", summary.read_text())


class DiffSelection(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repo = Path(directory.name)
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Scope tests")
        self.git("config", "user.email", "scope@example.invalid")
        self.base = self.commit("README.md", "initial")

    def git(self, *args):
        return subprocess.check_output(
            ["git", *args], cwd=self.repo, text=True, stderr=subprocess.PIPE
        ).strip()

    def commit(self, path, content):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        self.git("add", "--", path)
        self.git("-c", "commit.gpgsign=false", "commit", "-m", "fixture")
        return self.git("rev-parse", "HEAD")

    def test_push_includes_all_commits_not_only_the_last(self):
        self.commit("integration/audio/app-audio.cjs", "changed")
        head = self.commit("README.md", "docs")
        result = event_scope(self.repo, "push", {"before": self.base, "after": head})
        self.assertEqual(enabled(result), {"unit", "audio", "packaging"})
        self.assertEqual(set(result["paths"]), {"README.md", "integration/audio/app-audio.cjs"})

    def test_pr_includes_entire_branch_without_unrelated_base_changes(self):
        self.git("checkout", "-b", "pr")
        self.commit("scripts/releases.py", "release helper")
        head = self.commit("README.md", "pr docs")
        self.git("checkout", "main")
        base = self.commit("integration/audio/app-audio.cjs", "unrelated base change")
        result = event_scope(
            self.repo,
            "pull_request",
            {
                "pull_request": {"base": {"sha": base}, "head": {"sha": head}},
            },
        )
        self.assertEqual(enabled(result), {"unit"})
        self.assertEqual(set(result["paths"]), {"README.md", "scripts/releases.py"})

    def test_rename_out_of_runtime_directory_still_selects_native_checks(self):
        base = self.commit("tests/runtime/frame_addon.cc", "native fixture")
        (self.repo / "docs").mkdir()
        self.git("mv", "tests/runtime/frame_addon.cc", "docs/old fixture\nnotes.md")
        self.git("-c", "commit.gpgsign=false", "commit", "-m", "move")
        paths = changed_paths(self.repo, base, self.git("rev-parse", "HEAD"))
        self.assertEqual(set(paths), {"tests/runtime/frame_addon.cc", "docs/old fixture\nnotes.md"})
        self.assertEqual(enabled(select(paths)), {"unit", "native"})

    def test_large_diff_is_not_truncated(self):
        for index in range(350):
            (self.repo / f"README-{index}.md").write_text("docs")
        self.git("add", ".")
        head = self.commit("scripts/patch_krisp.py", "native change")
        paths = changed_paths(self.repo, self.base, head)
        self.assertEqual(len(paths), 351)
        self.assertIn("scripts/patch_krisp.py", paths)

    def test_missing_history_and_new_branch_fall_back_to_full(self):
        for base in ("0" * 40, "f" * 40):
            with self.subTest(base=base):
                result = event_scope(self.repo, "push", {"before": base, "after": self.base})
                self.assertEqual(enabled(result), set(SCOPES))
                self.assertIn("Cannot determine", result["reason"])

    def test_git_failure_never_silently_skips_checks(self):
        with patch("ci_scope.changed_paths", side_effect=OSError("cannot read repository")):
            result = event_scope(self.repo, "push", {"before": self.base, "after": self.base})
        self.assertEqual(enabled(result), set(SCOPES))
