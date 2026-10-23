"""Verify hook recovery cannot reset a repository outside the build workspace."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_reference import build_config, build_inputs, enable_compiler_cache, reference_inputs, reset_patch_repositories, reuse_completed, verify_source
from common import ROOT, digest, read_json, sha256, write_json


class HookRecovery(unittest.TestCase):
    def test_cached_source_rejects_both_staged_and_unstaged_drift(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "source"
            head = self.repo(repo)
            report = {"upstream_commit":head, "tree":self.git(repo, "write-tree")}
            verify_source(repo, report)
            (repo / "fixture").write_text("unrecorded edit\n")
            with self.assertRaisesRegex(ValueError, "unrecorded edits"):
                verify_source(repo, report)
            self.git(repo, "add", "fixture")
            with self.assertRaisesRegex(ValueError, "differs from its assembly report"):
                verify_source(repo, report)

    def git(self, repo, *args):
        return subprocess.check_output(["git", "-C", str(repo), "-c", "user.name=Test",
            "-c", "user.email=test@example.invalid", *args], text=True, stderr=subprocess.DEVNULL).strip()

    def repo(self, path):
        path.mkdir()
        self.git(path, "init", "--quiet")
        (path / "fixture").write_text("base\n")
        self.git(path, "add", "fixture")
        self.git(path, "commit", "--quiet", "-m", "base")
        return self.git(path, "rev-parse", "HEAD")

    def test_recovery_returns_to_pre_patch_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "dependency"
            base = self.repo(repo)
            (repo / "fixture").write_text("patch\n")
            self.git(repo, "commit", "--quiet", "-am", "dependency patch")
            (repo / "fixture").write_text("interrupted hook\n")
            reset_patch_repositories(root, {"dependency": base})
            self.assertEqual((repo / "fixture").read_text(), "base\n")
            self.assertEqual(self.git(repo, "rev-parse", "HEAD"), base)

    def test_recovery_refuses_symlink_outside_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "build"
            workspace.mkdir()
            outside = root / "outside"
            base = self.repo(outside)
            (outside / "fixture").write_text("must stay\n")
            (workspace / "dependency").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                reset_patch_repositories(workspace, {"dependency": base})
            self.assertEqual((outside / "fixture").read_text(), "must stay\n")


class CompletedBuild(unittest.TestCase):
    def test_cache_wrapper_does_not_invalidate_existing_reference_objects(self):
        with tempfile.TemporaryDirectory() as temp:
            args = Path(temp) / "args.gn"
            self.assertTrue(enable_compiler_cache("reference", args))
            args.write_text('is_debug = false\n')
            self.assertFalse(enable_compiler_cache("reference", args))
            self.assertTrue(enable_compiler_cache("stable", args))
            args.write_text('cc_wrapper = "/path/to/sccache"\n')
            self.assertTrue(enable_compiler_cache("reference", args))

    def test_reuse_requires_matching_identity_archive_and_success(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp) / "cache"
            output = Path(temp) / "output"
            output.mkdir()
            archive = work / "checkout/src/out/Release/dist.zip"
            archive.parent.mkdir(parents=True)
            archive.write_bytes(b"fixture archive")
            report = {"build_key": "key", "archive_sha256": sha256(archive),
                      "source": {"target": "stable", "source_assembled": True,
                                 "dependencies_patched": True, "compiled": True}}
            write_json(work / "completed.json", report)
            with patch("build_reference.run", side_effect=AssertionError("must not start a build")):
                self.assertTrue(reuse_completed(work, output, "key"))
            self.assertEqual((output / "runtime-dist.zip").read_bytes(), archive.read_bytes())
            self.assertTrue(read_json(output / "capacity.json")["reused_completed_artifact"])
            self.assertFalse(reuse_completed(work, output, "changed"))
            report["source"]["compiled"] = False
            write_json(work / "completed.json", report)
            self.assertFalse(reuse_completed(work, output, "key"))
            report["source"]["compiled"] = True
            write_json(work / "completed.json", report)
            archive.write_bytes(b"damaged")
            self.assertFalse(reuse_completed(work, output, "key"))

    def test_reference_workspace_stays_reusable_and_candidates_use_v3(self):
        sources = read_json(ROOT / "config/sources.lock.json")
        reference = build_config("reference")
        self.assertEqual(digest(reference_inputs(sources["fork"], reference)),
                         "be92ed7160913c8b53ae06e6003e6de864d292f1199af9420a8c6dd22ec72aa4")
        keys = set()
        for target in ("prerelease",):
            config = build_config(target)
            self.assertEqual(config["cpu_baseline"], "x86-64-v3")
            _, inputs, key = build_inputs(target, sources, config)
            self.assertNotEqual(inputs["config"], reference)
            keys.add(key)
        self.assertEqual(len(keys), 1)


if __name__ == "__main__":
    unittest.main()
