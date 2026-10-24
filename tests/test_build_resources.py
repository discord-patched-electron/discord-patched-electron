import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from assemble import prepare_cache
from build_reference import build_config, build_inputs
from build_resources import GIB, limits, select
from common import ROOT, read_json


class Scheduling(unittest.TestCase):
    def test_current_container_gets_four_jobs_and_one_link(self):
        plan = select(4, 12 * GIB)
        self.assertEqual(plan["parallel_jobs"], 4)
        self.assertEqual(plan["sync_jobs"], 4)
        self.assertEqual(plan["concurrent_links"], 1)

    def test_memory_fractional_cpu_and_explicit_overrides(self):
        self.assertEqual(select(20, 8 * GIB)["parallel_jobs"], 3)
        self.assertEqual(select(1.5, 32 * GIB)["parallel_jobs"], 1)
        self.assertEqual(select(96, 256 * GIB)["parallel_jobs"], 32)
        self.assertEqual(select(4, 12 * GIB, "2")["parallel_jobs"], 2)
        for value in ("0", "33", "-1", "broken", "4.5"):
            with self.assertRaises(ValueError):
                select(4, 12 * GIB, value)

    def test_container_quota_affinity_and_parent_limits(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group = root / "cgroup"
            child = group / "parent/child"
            child.mkdir(parents=True)
            (root / "membership").write_text("0::/parent/child\n")
            (root / "meminfo").write_text(f"MemTotal: {32 * GIB // 1024} kB\n")
            (group / "cpu.max").write_text("max 100000")
            (group / "memory.max").write_text("max")
            (child.parent / "cpu.max").write_text("400000 100000")
            (child.parent / "memory.max").write_text(str(12 * GIB))
            (child / "cpu.max").write_text("800000 100000")
            (child / "memory.max").write_text(str(24 * GIB))
            with patch("build_resources.os.sched_getaffinity", return_value=set(range(20))):
                self.assertEqual(
                    limits(group, root / "membership", root / "meminfo"), (4, 12 * GIB)
                )
            with patch("build_resources.os.sched_getaffinity", return_value={0, 1}):
                self.assertEqual(
                    limits(group, root / "membership", root / "meminfo"), (2, 12 * GIB)
                )


class ActiveChannels(unittest.TestCase):
    def test_disabled_channels_cannot_be_built_or_assembled(self):
        sources = read_json(ROOT / "config/sources.lock.json")
        for channel in ("stable", "nightly"):
            with self.assertRaisesRegex(ValueError, "Disabled Electron channel"):
                build_config(channel)
            with self.assertRaisesRegex(ValueError, "Disabled Electron channel"):
                build_inputs(channel, sources, {})
            with tempfile.TemporaryDirectory() as temp:
                cache = Path(temp) / "cache.git"
                with self.assertRaisesRegex(ValueError, "Disabled Electron channel"):
                    prepare_cache(cache, sources, channel)
                self.assertFalse(cache.exists())

    def test_candidate_source_fetches_only_patch_base_and_selected_commit(self):
        sources = read_json(ROOT / "config/sources.lock.json")
        with (
            tempfile.TemporaryDirectory() as temp,
            patch("assemble.run") as run,
            patch("assemble.subprocess.run") as exists,
        ):
            exists.return_value.returncode = 1
            prepare_cache(Path(temp), sources, "prerelease")
            fetches = [call.args for call in run.call_args_list if "fetch" in call.args]
            self.assertEqual(len(fetches), 2)
            self.assertEqual(
                fetches[0][-2:], (sources["fork"]["repository"], sources["fork"]["base"])
            )
            self.assertEqual(
                fetches[1][-2:],
                (sources["upstream_repository"], sources["targets"]["prerelease"]["commit"]),
            )


if __name__ == "__main__":
    unittest.main()
