import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from assemble import hydrate_patch_base


class PartialSourceCache(unittest.TestCase):
    def test_cold_candidate_fetches_abbreviated_diff_base_blobs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            origin, cache, checkout = (root / name for name in ("origin", "cache.git", "candidate"))

            def git(directory, *args):
                return subprocess.check_output(
                    ["git", "-C", str(directory), *args], text=True, stderr=subprocess.DEVNULL
                ).strip()

            subprocess.run(["git", "init", "-q", str(origin)], check=True)
            git(origin, "config", "user.name", "Fixture")
            git(origin, "config", "user.email", "fixture@example.invalid")
            git(origin, "config", "uploadpack.allowFilter", "true")
            git(origin, "config", "uploadpack.allowAnySHA1InWant", "true")
            lines = [f"line {i}\n" for i in range(50)]
            (origin / "code.txt").write_text("".join(lines))
            git(origin, "add", "code.txt")
            git(origin, "commit", "-qm", "base")
            base = git(origin, "rev-parse", "HEAD")
            lines[10] = "Discord addition\n"
            (origin / "code.txt").write_text("".join(lines))
            git(origin, "commit", "-qam", "fork")
            fork = git(origin, "rev-parse", "HEAD")
            patch = root / "import.patch"
            patch.write_text(git(origin, "diff", "--abbrev=9", base, fork) + "\n")
            git(origin, "checkout", "--detach", base)
            lines[10], lines[40] = "line 10\n", "Upstream addition\n"
            (origin / "code.txt").write_text("".join(lines))
            git(origin, "commit", "-qam", "upstream")
            upstream = git(origin, "rev-parse", "HEAD")
            subprocess.run(
                ["git", "clone", "-q", "--bare", "--filter=blob:none", origin.as_uri(), str(cache)],
                check=True,
            )
            git(cache, "worktree", "add", "--detach", str(checkout), upstream)
            prefix = re.search(r"^index (\w+)\.\.", patch.read_text(), re.M)[1]
            missing = subprocess.run(
                ["git", "-C", str(cache), "cat-file", "-e", prefix], capture_output=True
            )
            self.assertNotEqual(missing.returncode, 0)
            hydrate_patch_base(
                cache, {"patch": str(patch), "base": base, "repository": origin.as_uri()}
            )
            self.assertEqual(git(cache, "cat-file", "-t", prefix), "blob")
            git(checkout, "apply", "--3way", "--index", str(patch))
            self.assertIn("Discord addition", (checkout / "code.txt").read_text())
            self.assertIn("Upstream addition", (checkout / "code.txt").read_text())
            # An already hydrated cache remains usable without the remote.
            hydrate_patch_base(
                cache, {"patch": str(patch), "base": base, "repository": "/missing-remote"}
            )


if __name__ == "__main__":
    unittest.main()
