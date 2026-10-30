import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import read_json, write_json
from state_branch import checkout, git, push_state, save


class StateBranch(unittest.TestCase):
    def test_transient_server_error_retries_identical_push(self):
        failed = subprocess.CompletedProcess(
            ["git", "push"], 1, "", "remote: Internal Server Error"
        )
        passed = subprocess.CompletedProcess(["git", "push"], 0, "", "")
        with (
            patch("state_branch.git", side_effect=[failed, passed]) as command,
            patch("state_branch.time.sleep") as sleep,
        ):
            push_state(Path("state"))
        self.assertEqual(command.call_count, 2)
        self.assertEqual(command.call_args_list[0], command.call_args_list[1])
        sleep.assert_called_once_with(2)

    def test_retry_limit_and_permanent_failures(self):
        for message, attempts in (
            ("The requested URL returned error: 503", 3),
            ("HEAD -> ci-state (fetch first)", 1),
            ("non-fast-forward; Internal Server Error", 1),
            ("Authentication failed", 1),
            ("Permission denied", 1),
        ):
            with (
                self.subTest(message=message),
                patch(
                    "state_branch.git",
                    return_value=subprocess.CompletedProcess(["git", "push"], 1, "", message),
                ) as command,
                patch("state_branch.time.sleep") as sleep,
            ):
                with self.assertRaises(subprocess.CalledProcessError):
                    push_state(Path("state"))
                self.assertEqual(command.call_count, attempts)
                self.assertEqual(sleep.call_count, attempts - 1)

    def test_accepted_push_with_lost_response_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            remote, local = root / "remote.git", root / "state"
            subprocess.run(["git", "init", "--quiet", "--bare", str(remote)], check=True)
            checkout(str(remote), local)
            write_json(local / "state.json", {"schema": 1, "reserved": "one-build"})
            pushes = []

            def lost_response(directory, *args, **kwargs):
                result = git(directory, *args, **kwargs)
                if args[0] == "push":
                    pushes.append(result)
                    if len(pushes) == 1:
                        return subprocess.CompletedProcess(result.args, 1, "", "Connection reset")
                return result

            with (
                patch("state_branch.git", side_effect=lost_response),
                patch("state_branch.time.sleep"),
            ):
                self.assertTrue(save(local))
            self.assertEqual(len(pushes), 2)
            checkout(str(remote), root / "verify")
            self.assertEqual(read_json(root / "verify/state.json")["reserved"], "one-build")
            self.assertEqual(
                git(
                    local, "rev-list", "--count", "HEAD", capture_output=True, text=True, check=True
                ).stdout.strip(),
                "1",
            )

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
