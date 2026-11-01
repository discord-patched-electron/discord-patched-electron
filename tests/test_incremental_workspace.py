"""Git-based reuse preserves unchanged source and resumes interrupted transitions."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_reference import verify_source
from common import read_json, write_json
from incremental_workspace import transition, workspace_id


def git(path, *args):
    return subprocess.check_output(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            *args,
        ],
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()


class Incremental(unittest.TestCase):
    def test_slot_changes_for_toolchain_or_major_but_not_electron_patch_version(self):
        inputs = {
            "fork": {},
            "config": {},
            "container": {},
            "candidate_toolchain": {},
            "target": {"version": "46.0.0-alpha.1", "commit": "old"},
        }
        with patch.dict(os.environ, {"ELECTRON_INCREMENTAL_WORKSPACE": "1"}):
            first = workspace_id("prerelease", inputs)
            inputs["target"] = {"version": "46.0.0-alpha.2", "commit": "new"}
            self.assertEqual(workspace_id("prerelease", inputs), first)
            inputs["target"]["version"] = "47.0.0-alpha.1"
            self.assertNotEqual(workspace_id("prerelease", inputs), first)
            inputs["target"]["version"] = "46.0.0-alpha.1"
            inputs["config"] = {"changed": True}
            self.assertNotEqual(workspace_id("prerelease", inputs), first)

    def test_git_transition_preserves_objects_and_unchanged_file_timestamps(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp)
            repo = cache / "source.git"
            git(cache, "init", "--bare", str(repo))
            seed = cache / "seed"
            git(cache, "clone", str(repo), str(seed))
            (seed / "unchanged.h").write_text("same")
            (seed / "changed.h").write_text("old")
            git(seed, "add", ".")
            git(seed, "commit", "-m", "old")
            old = git(seed, "rev-parse", "HEAD")
            git(seed, "push", "origin", "HEAD:main")
            work = cache / "prerelease/incremental-fixture"
            electron = work / "checkout/src/electron"
            electron.parent.mkdir(parents=True)
            git(repo, "worktree", "add", "--detach", str(electron), old)
            old_report = {
                "upstream_commit": old,
                "tree": git(electron, "write-tree"),
                "source_assembled": True,
            }
            write_json(work / "source.json", old_report)
            write_json(work / "native-inputs.json", {"version": "old"})
            for name in ("prepared", "synchronized", "completed"):
                write_json(work / (name + ".json"), {})
            obj = work / "checkout/src/out/Release/retained.o"
            obj.parent.mkdir(parents=True)
            obj.write_bytes(b"object")
            stamp = (electron / "unchanged.h").stat().st_mtime_ns
            (seed / "changed.h").write_text("new")
            git(seed, "commit", "-am", "new")
            new = git(seed, "rev-parse", "HEAD")
            git(seed, "push", "origin", "HEAD:main")

            def assemble(_target, shared, destination, report):
                git(shared, "worktree", "add", "--detach", str(destination), new)
                write_json(
                    report,
                    {
                        "upstream_commit": new,
                        "tree": git(destination, "write-tree"),
                        "source_assembled": True,
                    },
                )
                return True

            def fail_reset(*_):
                raise RuntimeError("interruption")

            # Exercise write-ahead recovery after a reset was interrupted.
            write_json(work / "patch-bases.json", {})
            with self.assertRaisesRegex(RuntimeError, "interruption"):
                transition(
                    work, "prerelease", {"version": "new"}, assemble, verify_source, fail_reset
                )
            self.assertTrue((work / "transition.json").exists())
            transition(
                work,
                "prerelease",
                {"version": "new"},
                lambda *_: self.fail("must reuse validated transition"),
                verify_source,
                lambda *_: None,
            )
            self.assertEqual((electron / "changed.h").read_text(), "new")
            self.assertEqual((electron / "unchanged.h").stat().st_mtime_ns, stamp)
            self.assertEqual(obj.read_bytes(), b"object")
            self.assertFalse((work / "transition.json").exists())
            self.assertFalse((work / "completed.json").exists())
            self.assertEqual(read_json(work / "native-inputs.json"), {"version": "new"})
            verify_source(electron, read_json(work / "source.json"))
