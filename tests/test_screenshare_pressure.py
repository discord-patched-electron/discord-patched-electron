import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests/screenshare"))
sys.path.insert(0, str(ROOT / "scripts"))
from benchmark_screenshare_load import cases
from pressure_session import Pressure


class ScreensharePressure(unittest.TestCase):
    def test_cleanup_never_starts_load_after_an_early_client_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pressure-phase").write_text("constrained")
            fixture = Pressure(root, dict(pressure="cpu", seconds=30, backend="pipewire"), {})
            with patch("pressure_session.subprocess.Popen") as start:
                fixture.close()
            start.assert_not_called()
            self.assertFalse(json.loads((root / "pressure.json").read_text())["passed"])

    def test_comparison_pairs_change_only_native_module_and_alternate_order(self):
        rows = cases(60, "memfd", 2, 4)
        self.assertEqual(rows, cases(60, "memfd", 2, 4))
        self.assertEqual(len(rows), 8)
        for a, b in zip(rows[::2], rows[1::2], strict=True):
            self.assertEqual(a["options"], b["options"])
            self.assertEqual(a["pair"], b["pair"])
            self.assertNotEqual(a["module"], b["module"])
        self.assertEqual(rows[0]["module"], "baseline")
        self.assertEqual(rows[4]["module"], "candidate")
        for args in ((24, "memfd", 2, 0), (60, "unknown", 2, 0), (60, "dmabuf", 1, 0)):
            with self.assertRaises(ValueError):
                cases(*args)

    def test_pressure_starts_once_and_failed_or_aborted_children_cannot_pass(self):
        for status in (0, 3, None):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                process = Mock(returncode=status)
                process.poll.return_value = status
                fixture = Pressure(root, dict(pressure="cpu", seconds=30, backend="pipewire"), {})
                with patch("pressure_session.subprocess.Popen", return_value=process) as start:
                    (root / "pressure-phase").write_text("initial")
                    fixture.poll()
                    start.assert_not_called()
                    with patch("pressure_session.os.sched_getaffinity", return_value={0}):
                        (root / "pressure-phase").write_text("constrained")
                        fixture.poll()
                        fixture.poll()
                    start.assert_called_once()
                    fixture.logs[0].write("ready\ncompleted batches=4\n")
                    fixture.close()
                report = json.loads((root / "pressure.json").read_text())
                self.assertEqual(report["passed"], status == 0)
                if status is None:
                    process.terminate.assert_called_once()
                    process.wait.assert_called_once()

    def test_combined_pressure_uses_each_affinity_cpu_and_one_gpu(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = dict(
                pressure="both", seconds=30, backend="pipewire", render_node="/dev/dri/renderD128"
            )
            fixture = Pressure(root, config, {})
            processes = [Mock(returncode=0) for _ in range(17)]
            for process in processes:
                process.poll.return_value = 0
            with (
                patch("pressure_session.os.sched_getaffinity", return_value=set(range(16))),
                patch("pressure_session.subprocess.Popen", side_effect=processes) as start,
            ):
                (root / "pressure-phase").write_text("constrained")
                fixture.poll()
                fixture.poll()
                commands = [call.args[0] for call in start.call_args_list]
                self.assertEqual(commands[:16], [[str(root / "pressure"), "cpu", "10"]] * 16)
                self.assertEqual(
                    commands[16], [str(root / "pressure"), "gpu", "10", "/dev/dri/renderD128"]
                )
                for log in fixture.logs:
                    log.write("ready\ncompleted batches=4\n")
                fixture.close()
            report = json.loads((root / "pressure.json").read_text())
            self.assertTrue(report["passed"])
            self.assertEqual(report["worker_modes"], ["cpu"] * 16 + ["gpu"])

    def test_unknown_unbounded_or_unscoped_pressure_is_rejected_before_start(self):
        valid = dict(
            pressure="both", seconds=30, backend="pipewire", render_node="/dev/dri/renderD128"
        )
        for change in (
            dict(pressure="unknown"),
            dict(seconds=31),
            dict(seconds=11),
            dict(seconds=True),
            dict(backend="x11"),
            dict(render_node=None),
        ):
            with self.subTest(change=change), patch("pressure_session.subprocess.Popen") as start:
                with self.assertRaises(ValueError):
                    Pressure(Path("/unused"), {**valid, **change}, {})
                start.assert_not_called()

    def test_controller_deadline_kills_hung_worker_and_cannot_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process = Mock(returncode=None)
            process.poll.return_value = None
            fixture = Pressure(root, dict(pressure="cpu", seconds=30, backend="pipewire"), {})
            with (
                patch("pressure_session.os.sched_getaffinity", return_value={0}),
                patch("pressure_session.subprocess.Popen", return_value=process),
                patch("pressure_session.time.monotonic", return_value=100) as monotonic,
            ):
                (root / "pressure-phase").write_text("constrained")
                fixture.poll()
                monotonic.return_value = 117
                fixture.poll()
                process.kill.assert_called_once()
                # A late success cannot erase the exceeded hard bound.
                process.returncode = 0
                process.poll.return_value = 0
                fixture.logs[0].write("ready\ncompleted batches=4\n")
                fixture.close()
            report = json.loads((root / "pressure.json").read_text())
            self.assertFalse(report["passed"])
            self.assertIn("timed-out", [event["event"] for event in report["events"]])

    def test_failed_gpu_start_reaps_already_started_cpu_workers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process = Mock(returncode=None)
            process.poll.return_value = None
            fixture = Pressure(
                root,
                dict(
                    pressure="both",
                    seconds=30,
                    backend="pipewire",
                    render_node="/dev/dri/renderD128",
                ),
                {},
            )
            with (
                patch("pressure_session.os.sched_getaffinity", return_value={0}),
                patch("pressure_session.subprocess.Popen", side_effect=[process, OSError("bad")]),
            ):
                (root / "pressure-phase").write_text("constrained")
                with self.assertRaises(OSError):
                    fixture.poll()
                fixture.close()
            process.terminate.assert_called_once()
            process.wait.assert_called_once()
            self.assertFalse(json.loads((root / "pressure.json").read_text())["passed"])

    @unittest.skipUnless(shutil.which("cc") and shutil.which("pkg-config"), "C toolchain required")
    def test_pressure_compiles_and_rejects_unbounded_work_without_opening_devices(self):
        flags = subprocess.check_output(
            ["pkg-config", "--cflags", "--libs", "gbm", "egl", "glesv2"], text=True
        ).split()
        with tempfile.TemporaryDirectory() as temporary:
            binary = str(Path(temporary) / "pressure")
            subprocess.run(
                [
                    "cc",
                    "-std=c11",
                    "-O2",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    str(ROOT / "tests/screenshare/pressure.c"),
                    *flags,
                    "-o",
                    binary,
                ],
                check=True,
            )
            for args in (
                [],
                ["gpu", "nan", "/dev/null"],
                ["cpu", "inf"],
                ["cpu", "0"],
                ["cpu", "16"],
                ["cpu", "1x"],
                ["unknown", "1"],
                ["gl", "0", "/unopened-render-node"],
                ["gl", "16", "/unopened-render-node"],
                ["gl", "1"],
                ["gl", "1", "/unopened-render-node", "4000"],
                ["gl", "1", "/unopened-render-node", "uncapped", "0"],
                ["gl", "1", "/unopened-render-node", "uncapped", "513"],
                ["gl", "1", "/unopened-render-node", "uncapped", "-1"],
                ["gl", "1", "/unopened-render-node", "240", "64x"],
                ["gl", "1", "/unopened-render-node", "240", ""],
                ["gl", "1", "/unopened-render-node", "240", "64", "extra"],
            ):
                self.assertEqual(subprocess.run([binary, *args], timeout=2).returncode, 2)


if __name__ == "__main__":
    unittest.main()
