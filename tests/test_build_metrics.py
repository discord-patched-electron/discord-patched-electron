import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_metrics import cpu_ticks, timed_run, utilization
from common import read_json


class BuildMetrics(unittest.TestCase):
    def test_cpu_measurement_excludes_idle_iowait_and_duplicate_guest_ticks(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "stat"
            path.write_text("cpu  10 2 5 80 3 0 0 0 5 2\ncpu0 1 2 3 4\n")
            self.assertEqual(cpu_ticks(path), (100, 17))
        self.assertEqual(utilization((100, 17), (200, 67)), 50)
        self.assertIsNone(utilization((100, 17), (100, 17)))

    def test_failure_still_records_phase_without_swallowing_the_error(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch("common.run", side_effect=subprocess.CalledProcessError(1, "fixture")),
        ):
            output = Path(temp)
            with self.assertRaises(subprocess.CalledProcessError):
                timed_run(output, "compilation", "fixture")
            phases = read_json(output / "phases.json")
            self.assertEqual(phases[0]["stage"], "compilation")
            self.assertFalse(phases[0]["success"])
