import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch as mock_patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_reference import build_config
from common import ROOT, read_json
from fingerprints import integration_key, runtime_key
from patch_app_audio import ANCHOR, CAMERA_BEFORE, patch


class AppAudioPatch(unittest.TestCase):
    def test_known_wrapper_is_idempotent_and_unknown_layout_fails_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            wrapper = directory / "index.js"
            wrapper.write_text(
                CAMERA_BEFORE
                + "\n"
                + ANCHOR
                + "    return instance;\n}\nmodule.exports = VoiceEngine;\n"
            )
            adapter = ROOT / "integration/audio/app-audio.cjs"
            report = patch(directory, adapter)
            content = wrapper.read_bytes()
            self.assertTrue(report["enabled_by_default"])
            self.assertEqual(report["default_sessions"], ["wayland"])
            self.assertEqual(report["disable_environment"], "DPE_APP_AUDIO=0")
            self.assertEqual(patch(directory, adapter)["output_sha256"], report["output_sha256"])
            self.assertEqual(wrapper.read_bytes(), content)
            wrapper.write_text("function changedConnection(instance) {}\n")
            content = wrapper.read_bytes()
            with self.assertRaisesRegex(ValueError, "Unknown"):
                patch(directory, adapter)
            self.assertEqual(wrapper.read_bytes(), content)


class AudioBuildIdentity(unittest.TestCase):
    def test_module_adapter_changes_cannot_invalidate_native_build_identity(self):
        sources = read_json(ROOT / "config/sources.lock.json")
        client = read_json(ROOT / "config/clients.lock.json")["clients"]["stable"]
        target = sources["targets"]["prerelease"]
        native = runtime_key(target, sources, build_config("prerelease"))
        original = integration_key(native, client, "prerelease")
        with mock_patch("fingerprints.hash_files", return_value={"application_audio": "changed"}):
            self.assertNotEqual(integration_key(native, client, "prerelease"), original)
            self.assertEqual(runtime_key(target, sources, build_config("prerelease")), native)


class AudioEvidenceGate(unittest.TestCase):
    def test_silence_and_cross_application_leakage_cannot_pass(self):
        import numpy as np
        from check_app_audio import analyze

        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            self.assertFalse(analyze(output)["all_cases_passed"])
            t = np.arange(24000) / 48000
            a = 5000 * np.sin(2 * np.pi * 440 * t)
            b = 5000 * np.sin(2 * np.pi * 880 * t)
            for name, samples in [("system", a + b), ("app-a", a), ("app-b", b), ("re-enabled", a)]:
                samples.astype("<i2").tofile(output / (name + ".raw"))
            self.assertTrue(analyze(output)["all_cases_passed"])
            (a + b).astype("<i2").tofile(output / "app-a.raw")
            self.assertFalse(analyze(output)["all_cases_passed"])

    def test_short_recordings_and_brief_cross_application_leaks_fail(self):
        import numpy as np
        from check_app_audio import analyze

        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            t = np.arange(48000) / 48000
            a = 5000 * np.sin(2 * np.pi * 440 * t)
            b = 5000 * np.sin(2 * np.pi * 880 * t)
            for name, samples in [("system", a + b), ("app-a", a), ("app-b", b), ("re-enabled", a)]:
                samples.astype("<i2").tofile(output / (name + ".raw"))
            self.assertTrue(analyze(output)["all_cases_passed"])
            for replacement in (a[:480], np.zeros(48000), b):
                with self.subTest(length=len(replacement)):
                    replacement.astype("<i2").tofile(output / "app-a.raw")
                    self.assertFalse(analyze(output)["cases"]["app-a"]["passed"])
            burst = a.copy()
            burst[480:960] += b[480:960]
            burst.astype("<i2").tofile(output / "app-a.raw")
            self.assertFalse(analyze(output)["cases"]["app-a"]["passed"])

    def test_silent_phases_reject_unrelated_frequencies_and_short_bursts(self):
        import numpy as np
        from check_app_audio import analyze

        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            t = np.arange(48000) / 48000
            unrelated = 5000 * np.sin(2 * np.pi * 1399 * t)
            burst = np.zeros(48000)
            burst[100:110] = 1000
            for phase in ("disabled", "unknown", "app-exited", "cleared"):
                for samples in (unrelated, burst):
                    with self.subTest(phase=phase, peak=float(np.max(samples))):
                        samples.astype("<i2").tofile(output / (phase + ".raw"))
                        self.assertFalse(analyze(output)["cases"][phase]["passed"])

    def test_missing_silent_phase_or_duplicate_completion_cannot_pass(self):
        from check_app_audio import validate_phases

        expected = ["audible", "silent"]
        validate_phases({"passed": True, "completed_phases": expected}, expected)
        for completed in (
            [],
            ["audible"],
            ["audible", "audible"],
            ["audible", "other"],
            "audible",
            [None, "silent"],
        ):
            with self.subTest(completed=completed), self.assertRaises(ValueError):
                validate_phases({"passed": True, "completed_phases": completed}, expected)
