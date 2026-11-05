import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from benchmark_capture_methods import (
    Run,
    counters,
    descendants,
    needs_fullscreen,
    summarize_resources,
)


class CaptureMethodsBenchmark(unittest.TestCase):
    def test_fullscreen_workload_is_not_toggled_back_into_a_tile(self):
        self.assertFalse(needs_fullscreen({"layout": {"window_size": [1920, 1080]}}))
        self.assertTrue(needs_fullscreen({"layout": {"window_size": [945, 1018]}}))
        self.assertTrue(needs_fullscreen({}))

    def test_children_from_non_main_threads_are_included(self):
        with tempfile.TemporaryDirectory() as folder:
            proc = Path(folder)
            for path, value in (
                ("1/task/1/children", "2"),
                ("1/task/9/children", "3"),
                ("3/task/3/children", "4"),
            ):
                file = proc / path
                file.parent.mkdir(parents=True)
                file.write_text(value)
            self.assertEqual(descendants(1, proc), {2, 3, 4})

    def test_duplicate_drm_descriptors_and_process_reuse(self):
        with tempfile.TemporaryDirectory() as folder:
            proc = Path(folder)
            (proc / "7/fdinfo").mkdir(parents=True)
            fields = ["S", *(["0"] * 19)]
            fields[11], fields[12], fields[19] = "20", "30", "300"
            (proc / "7/stat").write_text("7 (process ) name) " + " ".join(fields))
            text = "drm-client-id: 5\ndrm-pdev: 0000:03:00.0\ndrm-engine-gfx: 50 ns\n"
            for fd in (4, 8):
                (proc / f"7/fdinfo/{fd}").write_text(text)
            actual = counters(7, proc)
            self.assertEqual(actual["cpuTicks"], 50)
            self.assertEqual(actual["name"], "process ) name")
            self.assertEqual(actual["drmClients"], {"0000:03:00.0/5": {"drm-engine-gfx": 50}})
        first = {"cpuTicks": 10, "startTicks": 30, "drmClients": {"gpu/5": {"gfx": 0}}}
        last = {"cpuTicks": 110, "startTicks": 30, "drmClients": {"gpu/5": {"gfx": 100000000}}}
        reused = dict(last, cpuTicks=99000, startTicks=50)
        samples = [
            {"seconds": 2, "groups": {"native": {"1": first, "2": first}}},
            {"seconds": 3, "groups": {"native": {"1": last, "2": last}}},
            {"seconds": 4, "groups": {"native": {"1": reused}}},
        ]
        summary = summarize_resources(samples)["native"]
        self.assertEqual(summary["cpuOneCorePercent"], 200 / os.sysconf("SC_CLK_TCK") / 2 * 100)
        self.assertEqual(summary["drmEnginePercent"], {"gfx": 5})

    def test_coalesced_protocol_lines_do_not_wait_for_a_second_read_event(self):
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                'import sys; print(\'{"id":1}\\n{"id":2}\', flush=True); sys.stdin.read()',
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        run = Run.__new__(Run)
        run.pending_lines, run.events = {}, []
        try:
            self.assertEqual(run.read(child, 2), {"id": 1})
            self.assertEqual(run.read(child, 0.05), {"id": 2})
        finally:
            child.communicate(timeout=2)


if __name__ == "__main__":
    unittest.main()
