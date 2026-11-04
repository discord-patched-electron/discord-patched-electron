import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch as mock_patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_reference import build_config
from common import ROOT, read_json
from fingerprints import integration_key, runtime_key
from patch_app_audio import ANCHOR, CAMERA_BEFORE, INSERT, VIDEO_INSERT, patch


class AppAudioPatch(unittest.TestCase):
    def test_existing_audio_wrapper_upgrades_and_rejected_native_stays_unpublished(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            wrapper = directory / "index.js"
            original = (
                CAMERA_BEFORE
                + "\nVoiceEngine.initialize({});\n"
                + ANCHOR
                + INSERT
                + "    return instance;\n}\nmodule.exports = VoiceEngine;\n"
            )
            wrapper.write_text(original)
            order = []

            def pulse(module):
                order.append("pulse")
                return {}

            def video(module):
                self.assertEqual(order, ["pulse"])
                raise ValueError("Unsupported H.264 fixture")

            adapter = ROOT / "integration/audio/app-audio.cjs"
            with (
                mock_patch("patch_app_audio.patch_pulse_audio", side_effect=pulse),
                mock_patch("patch_app_audio.patch_vaapi_hrd", side_effect=video),
            ):
                with self.assertRaisesRegex(ValueError, "Unsupported H.264"):
                    patch(directory, adapter)
            self.assertEqual(wrapper.read_text(), original)
            self.assertFalse((directory / "dpe-video.cjs").exists())
            with (
                mock_patch("patch_app_audio.patch_pulse_audio", return_value={}),
                mock_patch("patch_app_audio.patch_vaapi_hrd", return_value={}),
                mock_patch("patch_app_audio.patch_capture_dimensions", return_value={}),
                mock_patch("patch_app_audio.patch_decoder_eagain", return_value={}),
                mock_patch("patch_app_audio.patch_capture_chroma", return_value={}),
            ):
                patch(directory, adapter)
                self.assertEqual(wrapper.read_text().count(ANCHOR + VIDEO_INSERT + INSERT), 1)
                self.assertEqual(wrapper.read_text().count(INSERT), 1)

    @mock_patch("patch_app_audio.patch_capture_chroma", return_value={"chroma_fixture": True})
    @mock_patch("patch_app_audio.patch_decoder_eagain", return_value={"decoder_fixture": True})
    @mock_patch(
        "patch_app_audio.patch_capture_dimensions", return_value={"dimensions_fixture": True}
    )
    @mock_patch("patch_app_audio.patch_vaapi_hrd", return_value={"video_fixture": True})
    @mock_patch("patch_app_audio.patch_pulse_audio", return_value={"fixture": True})
    def test_known_wrapper_is_idempotent_and_unknown_layout_fails_unchanged(
        self, native, video, dimensions, decoder, chroma
    ):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            wrapper = directory / "index.js"
            wrapper.write_text(
                CAMERA_BEFORE
                + "\nVoiceEngine.initialize({});\n"
                + ANCHOR
                + "    return instance;\n}\nmodule.exports = VoiceEngine;\n"
            )
            adapter = ROOT / "integration/audio/app-audio.cjs"
            report = patch(directory, adapter)
            self.assertEqual(
                (directory / "system-tools.cjs").read_bytes(),
                (ROOT / "integration/audio/system-tools.cjs").read_bytes(),
            )
            native.assert_called_once_with(directory / "discord_voice.node")
            video.assert_called_once_with(directory / "discord_voice.node")
            dimensions.assert_called_once_with(directory / "discord_voice.node")
            chroma.assert_called_once_with(directory / "discord_voice.node")
            self.assertEqual(report["native_buffer_patch"], {"fixture": True})
            self.assertEqual(report["native_video_patch"], {"video_fixture": True})
            self.assertEqual(
                report["native_capture_dimensions_patch"], {"dimensions_fixture": True}
            )
            self.assertEqual(
                (directory / "dpe-video.cjs").read_bytes(),
                (ROOT / "integration/video/transport.cjs").read_bytes(),
            )
            for name in (
                "runtime.cjs",
                "ui-runtime.cjs",
                "PREACT-LICENSE.txt",
                "source.cjs",
                "components.css",
                "presentation.cjs",
                "stream-audio.cjs",
                "icons.cjs",
                "BOOTSTRAP-ICONS-LICENSE.txt",
            ):
                self.assertEqual(
                    (directory / "discord-ui" / name).read_bytes(),
                    (ROOT / "integration/discord-ui" / name).read_bytes(),
                )
            subprocess.run(
                [
                    "node",
                    "-e",
                    "require(process.argv[1])",
                    str(directory / "native-picker-bridge.cjs"),
                ],
                check=True,
            )
            content = wrapper.read_bytes()
            self.assertEqual(content.count(b"require('./dpe-video.cjs').wrap(instance);"), 1)
            self.assertEqual(
                content.count(b"require('./dpe-camera-flip.cjs').install(__dirname);"), 1
            )
            self.assertLess(
                content.index(b"dpe-camera-flip.cjs"), content.index(b"VoiceEngine.initialize")
            )
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
