from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch as mock_patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from common import ROOT, read_json
from fingerprints import integration_key, runtime_key
from build_reference import build_config
from patch_app_audio import patch, ANCHOR


class AppAudioPatch(unittest.TestCase):
    def test_known_wrapper_is_idempotent_and_unknown_layout_fails_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            wrapper = directory / 'index.js'
            wrapper.write_text(ANCHOR + '    return instance;\n}\nmodule.exports = VoiceEngine;\n')
            adapter = ROOT / 'integration/audio/app-audio.cjs'
            report = patch(directory, adapter)
            content = wrapper.read_bytes()
            self.assertTrue(report['enabled_by_default'])
            self.assertEqual(report['default_sessions'], ['wayland'])
            self.assertEqual(report['disable_environment'], 'DPE_APP_AUDIO=0')
            self.assertEqual(patch(directory, adapter)['output_sha256'], report['output_sha256'])
            self.assertEqual(wrapper.read_bytes(), content)
            wrapper.write_text('function changedConnection(instance) {}\n')
            content = wrapper.read_bytes()
            with self.assertRaisesRegex(ValueError, 'Unknown'):
                patch(directory, adapter)
            self.assertEqual(wrapper.read_bytes(), content)


class AudioBuildIdentity(unittest.TestCase):
    def test_module_adapter_changes_cannot_invalidate_native_build_identity(self):
        sources = read_json(ROOT / 'config/sources.lock.json')
        client = read_json(ROOT / 'config/clients.lock.json')['clients']['stable']
        target = sources['targets']['stable']
        native = runtime_key(target, sources, build_config('stable'))
        original = integration_key(native, client, 'stable')
        with mock_patch('fingerprints.hash_files', return_value={'application_audio': 'changed'}):
            self.assertNotEqual(integration_key(native, client, 'stable'), original)
            self.assertEqual(runtime_key(target, sources, build_config('stable')), native)


class AudioEvidenceGate(unittest.TestCase):
    def test_silence_and_cross_application_leakage_cannot_pass(self):
        import numpy as np
        from check_app_audio import analyze
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            self.assertFalse(analyze(output)['all_cases_passed'])
            t = np.arange(24000) / 48000
            a = 5000 * np.sin(2 * np.pi * 440 * t)
            b = 5000 * np.sin(2 * np.pi * 880 * t)
            for name, samples in [('system', a+b), ('app-a', a), ('app-b', b), ('re-enabled', a)]:
                samples.astype('<i2').tofile(output / (name + '.raw'))
            self.assertTrue(analyze(output)['all_cases_passed'])
            (a+b).astype('<i2').tofile(output / 'app-a.raw')
            self.assertFalse(analyze(output)['all_cases_passed'])
