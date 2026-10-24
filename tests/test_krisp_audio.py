"""Ensure reduced noise cannot pass the gate by muting or bypassing speech."""

import array
import shutil
import sys
import tempfile
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check_krisp_audio import analyze, generate, read_wav
from common import write_json


class AudioGate(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.fixture, self.probe = self.root / "fixture", self.root / "probe"
        generate(self.fixture)
        shutil.copytree(self.fixture / "input", self.probe / "audio-input")
        (self.probe / "profile/voice").mkdir(parents=True)
        (self.probe / "profile/voice/discord-webrtc_0").write_text(
            "Krisp initialized successfully\n"
        )
        self.report = {
            "passed": True,
            "audio_batches": {
                "errors": [],
                "batches": [
                    {"enabled": enabled, "processed": 1, "failed": 0}
                    for enabled in [False, True, False]
                ],
            },
            "versions": {},
            "original_inputs": {},
            "fixture_inputs": {},
        }
        write_json(self.probe / "runtime-report.json", self.report)
        for i in range(3):
            directory = self.probe / "profile" / f"audio-{i}"
            directory.mkdir()
            shutil.copy2(self.fixture / "input/noisy.wav", directory / "noisy_processed.wav")
        self.enabled = self.probe / "profile/audio-1/noisy_processed.wav"

    def test_bypassed_processing_fails(self):
        self.assertFalse(analyze(self.fixture, self.probe)["offline_krisp_verified"])

    def test_clean_speech_passes_but_muting_fails(self):
        shutil.copy2(self.fixture / "reference.wav", self.enabled)
        self.assertTrue(analyze(self.fixture, self.probe)["offline_krisp_verified"])
        samples = read_wav(self.enabled)
        with wave.open(str(self.enabled), "wb") as target:
            target.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
            target.writeframes(array.array("h", [0] * len(samples)).tobytes())
        result = analyze(self.fixture, self.probe)
        self.assertTrue(result["checks"]["noise_reduction_at_least_15db"])
        self.assertFalse(result["offline_krisp_verified"])
        self.assertFalse(result["checks"]["speech_gain_retained"])

    def test_processing_error_and_changed_input_are_rejected(self):
        self.report["audio_batches"]["errors"] = ["initialization failed"]
        write_json(self.probe / "runtime-report.json", self.report)
        with self.assertRaisesRegex(ValueError, "processing failed"):
            analyze(self.fixture, self.probe)
        self.report["audio_batches"]["errors"] = []
        write_json(self.probe / "runtime-report.json", self.report)
        shutil.copy2(self.fixture / "reference.wav", self.probe / "audio-input/noisy.wav")
        with self.assertRaisesRegex(ValueError, "input differs"):
            analyze(self.fixture, self.probe)


if __name__ == "__main__":
    unittest.main()
