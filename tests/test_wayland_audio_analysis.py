import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check_wayland_audio import analyze, pack_fixture
from common import ROOT, read_json


class AudioFixtureIdentity(unittest.TestCase):
    def test_packed_fixture_supplies_channel_identity_without_changing_source(self):
        source = ROOT / "tests/wayland/package.json"
        before = source.read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for channel, identity in (
                ("stable", "discord"),
                ("ptb", "discord-ptb"),
                ("canary", "discord-canary"),
            ):
                with self.subTest(channel=channel):
                    archive = directory / (channel + ".asar")
                    pack_fixture(archive, channel)
                    extracted = directory / channel
                    subprocess.run(
                        [
                            str(ROOT / "node_modules/.bin/asar"),
                            "extract",
                            str(archive),
                            str(extracted),
                        ],
                        check=True,
                    )
                    metadata = read_json(extracted / "package.json")
                    self.assertEqual(metadata["desktopName"], identity + ".desktop")
                    self.assertEqual(metadata["name"], "discord-application-audio-fixture")
                    self.assertEqual(
                        (extracted / metadata["main"]).read_bytes(),
                        (ROOT / "tests/wayland/main.cjs").read_bytes(),
                    )
            self.assertEqual(source.read_bytes(), before)


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

    def test_every_phase_rejects_missing_audio_or_brief_audio_after_stop(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            empty = analyze(directory)
            for phase in ("app-a", "app-b", "system", "re-enabled"):
                self.assertFalse(empty["cases"][phase]["passed"])
            burst = np.zeros(48000, dtype="<i2")
            burst[100:110] = 1000
            for phase in ("disabled", "audio-cancelled", "cancelled", "app-exited", "cleared"):
                with self.subTest(phase=phase):
                    burst.tofile(directory / (phase + ".raw"))
                    self.assertFalse(analyze(directory)["cases"][phase]["passed"])
