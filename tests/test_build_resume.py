"""Verify hook recovery cannot reset a repository outside the build workspace."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_reference import reset_patch_repositories


class HookRecovery(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
