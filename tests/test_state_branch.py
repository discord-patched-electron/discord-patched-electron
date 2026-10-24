import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import read_json, write_json
from state_branch import checkout, git, save


class StateBranch(unittest.TestCase):
    def test_unchanged_state_does_not_commit_and_racing_writer_cannot_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            remote = root / "remote.git"
            subprocess.run(["git", "init", "--quiet", "--bare", str(remote)], check=True)
            a = root / "a"
            checkout(str(remote), a)
            write_json(a / "state.json", {"schema": 1, "releases": ["seed"]})
            self.assertTrue(save(a))
            first = git(a, "rev-parse", "HEAD", capture_output=True, text=True, check=True).stdout
            self.assertFalse(save(a))
            self.assertEqual(
                first,
                git(a, "rev-parse", "HEAD", capture_output=True, text=True, check=True).stdout,
            )
            b = root / "b"
            checkout(str(remote), b)
            write_json(a / "state.json", {"schema": 1, "releases": ["seed", "stable-1"]})
            self.assertTrue(save(a))
            write_json(b / "state.json", {"schema": 1, "releases": ["seed", "stable-2"]})
            with self.assertRaises(subprocess.CalledProcessError):
                save(b)
            c = root / "c"
            checkout(str(remote), c)
            self.assertEqual(read_json(c / "state.json")["releases"], ["seed", "stable-1"])
            write_json(
                c / "state.json", {"schema": 1, "releases": ["seed", "stable-1", "stable-2"]}
            )
            self.assertTrue(save(c))
            files = git(
                c, "ls-tree", "--name-only", "HEAD", capture_output=True, text=True, check=True
            ).stdout
            self.assertEqual(files.strip(), "state.json")


if __name__ == "__main__":
    unittest.main()
