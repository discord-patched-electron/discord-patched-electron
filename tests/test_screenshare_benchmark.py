import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from benchmark_screenshare import aggregate, cases, summarize


class ScreenshareBenchmark(unittest.TestCase):
    def test_matrix_is_repeatable_balanced_and_changes_one_factor_per_pair(self):
        plan = cases("hardware", 3, 42)
        self.assertEqual(plan, cases("hardware", 3, 42))
        self.assertEqual(len(plan), 48)
        self.assertEqual(len({case["id"] for case in plan}), len(plan))
        for first, second in zip(plan[::2], plan[1::2], strict=True):
            self.assertEqual(first["pair"], second["pair"])
            self.assertEqual(
                {k: v for k, v in first["options"].items() if k != "capture_mode"},
                {k: v for k, v in second["options"].items() if k != "capture_mode"},
            )
            self.assertNotEqual(first["options"]["capture_mode"], second["options"]["capture_mode"])
        self.assertEqual(plan[0]["options"]["capture_mode"], "observe")
        self.assertEqual(plan[16]["options"]["capture_mode"], "latest")

    def test_software_profile_and_invalid_plan(self):
        for case in cases("software", 2, 1):
            self.assertEqual(case["options"]["memory"], "memfd")
            self.assertEqual(case["options"]["encoder"], "vp8")
        for profile, repetitions in (("unknown", 3), ("software", 1), ("hardware", 11)):
            with self.assertRaises(ValueError):
                cases(profile, repetitions, 0)

    def test_invalid_runs_are_retained_and_prevent_aggregate_performance_claims(self):
        case = cases("software", 2, 0)[0]
        report = dict(
            passed=True,
            measurements=dict(
                frames=[dict(arrivedMs=10), dict(arrivedMs=30), dict(arrivedMs=50)],
                phases=dict(initial=dict(sourceToReceiverMs=dict(p95=20))),
            ),
        )
        result = summarize(case, report)
        self.assertEqual(result["unique_fps"], 50)
        summary = aggregate([result])[case["variant"]]
        self.assertEqual(summary["median_run_p95_ms"], 20)
        summary = aggregate([result, dict(result, passed=False)])[case["variant"]]
        self.assertEqual(summary, dict(runs=2, valid_runs=1))


if __name__ == "__main__":
    unittest.main()
