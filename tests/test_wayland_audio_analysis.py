import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check_wayland_audio import analyze


class AudioEvidence(unittest.TestCase):
    def test_callback_phase_gaps_do_not_hide_brief_cross_application_leakage(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            t = np.arange(480) / 48000

            def tone(frequencies):
                return np.concatenate(
                    [
                        sum(5000 * np.sin(2 * np.pi * hz * t + frame * 0.731) for hz in frequencies)
                        for frame in range(100)
                    ]
                ).astype("<i2")

            for phase, frequencies in [
                ("app-a", (440, 660)),
                ("app-b", (997,)),
                ("system", (440, 660, 997)),
                ("re-enabled", (440, 660)),
            ]:
                tone(frequencies).tofile(directory / (phase + ".raw"))
            self.assertTrue(analyze(directory)["all_cases_passed"])
            # Only one 10 ms frame leaks the other app: a median-only check
            # would miss it, but the maximum clean-frame assertion rejects it.
            samples = tone((440, 660))
            samples[480:960] += (2000 * np.sin(2 * np.pi * 997 * t)).astype("<i2")
            samples.tofile(directory / "app-a.raw")
            self.assertFalse(analyze(directory)["cases"]["app-a"]["passed"])
            tone((440,)).tofile(directory / "app-a.raw")
            self.assertFalse(analyze(directory)["cases"]["app-a"]["passed"])
            # Even a sub-frame burst is forbidden after the share is disabled.
            np.full(100, 2000, dtype="<i2").tofile(directory / "disabled.raw")
            self.assertFalse(analyze(directory)["cases"]["disabled"]["passed"])
