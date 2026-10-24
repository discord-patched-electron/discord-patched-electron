import io
import sys
import tarfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from inspect_bundled_runtime import inspect_archive, inspect_clients


class BundledRuntime(unittest.TestCase):
    def inspect(self, executable=b"ELF Electron/44.9.3\x00", asar=b"bootstrap", library=b"library"):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w") as tar:
            for name, data in [
                ("DiscordCanary", executable),
                ("resources/app.asar", asar),
                ("libffmpeg.so", library),
            ]:
                entry = tarfile.TarInfo("files/" + name)
                entry.size = len(data)
                tar.addfile(entry, io.BytesIO(data))
        process = Mock(stdout=io.BytesIO(output.getvalue()))
        process.wait.return_value = 0
        process.poll.return_value = 0
        with patch("inspect_bundled_runtime.subprocess.Popen", return_value=process):
            return inspect_archive(Path("unused"), "canary")

    def test_asar_change_is_not_misidentified_as_new_electron(self):
        original = self.inspect()
        self.assertEqual(original, self.inspect(asar=b"new Discord bootstrap"))
        self.assertEqual(original["embedded_electron_version"], "44.9.3")

    def test_same_version_new_executable_or_library_is_detected(self):
        original = self.inspect()
        changed = self.inspect(executable=b"changed build Electron/44.9.3\x00")
        self.assertNotEqual(original["executable_sha256"], changed["executable_sha256"])
        self.assertEqual(
            original["embedded_electron_version"], changed["embedded_electron_version"]
        )
        self.assertNotEqual(
            original["payload_sha256"], self.inspect(library=b"new ffmpeg")["payload_sha256"]
        )

    def test_unchanged_core_is_not_downloaded_or_inspected_again(self):
        state = {
            "clients": {"canary": {"sources": {"core": {"sha256": "a" * 64}}}},
            "bundled_runtimes": {"canary": {"core_sha256": "a" * 64}},
        }
        with patch("inspect_bundled_runtime.download") as download:
            inspect_clients(state, Path("unused"))
            download.assert_not_called()
        self.assertEqual(state["bundled_runtime_errors"], {})
