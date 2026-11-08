import copy
import hashlib
import io
import json
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import digest
from import_local_runtime import import_runtime


class LocalRuntimeImport(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name) / "imported"
        memory = io.BytesIO()
        with zipfile.ZipFile(memory, "w") as archive:
            entry = zipfile.ZipInfo("electron")
            entry.external_attr = (stat.S_IFREG | 0o755) << 16
            archive.writestr(entry, b"owned runtime fixture")
        self.binary = memory.getvalue()
        self.archive_sha = hashlib.sha256(self.binary).hexdigest()
        self.key = "a" * 64
        self.capacity = {
            "runtime_key": self.key,
            "build_key": self.key,
            "archive_sha256": self.archive_sha,
            "archive_bytes": len(self.binary),
            "source": {"tree": "b" * 40},
        }
        self.provenance = {
            "compiler_provider": "local",
            "runtime_key": self.key,
            "archive_sha256": self.archive_sha,
            "archive_bytes": len(self.binary),
            "source_tree": "b" * 40,
            "publishable": False,
        }

    def inputs(self, capacity=None, provenance=None):
        self.assets = {
            "runtime-dist.zip": self.binary,
            "capacity.json": json.dumps(capacity or self.capacity).encode(),
            "local-build-provenance.json": json.dumps(provenance or self.provenance).encode(),
        }
        return {
            "schema": 1,
            "runtime_key": self.key,
            "assets": {
                name: hashlib.sha256(data).hexdigest() for name, data in self.assets.items()
            },
        }

    def download(self, url, destination, expected):
        name = destination.name
        self.assertEqual(
            url,
            "https://github.com/discord-patched-electron/cache/releases/download/"
            + "runtime-cache-"
            + self.key
            + "/"
            + name,
        )
        destination.write_bytes(self.assets[name])
        return destination

    def test_pinned_import_checks_native_report_and_preserves_local_provenance(self):
        pin = self.inputs()
        with (
            patch("import_local_runtime.download", side_effect=self.download) as download,
            patch("import_local_runtime.validate_capacity") as validate,
        ):
            self.assertEqual(import_runtime(self.output, pin), self.capacity)
        validate.assert_called_once_with(self.capacity, True)
        self.assertEqual(download.call_count, 3)
        self.assertEqual((self.output / "runtime/electron").read_bytes(), b"owned runtime fixture")
        self.assertTrue((self.output / "runtime/electron").stat().st_mode & 0o111)
        self.assertEqual(
            digest(json.loads((self.output / "capacity/local-build-provenance.json").read_text())),
            digest(self.provenance),
        )

    def test_invalid_pins_do_not_download(self):
        valid = self.inputs()
        for change in ({"runtime_key": "../other"}, {"schema": 2}, {"assets": {"other": "a" * 64}}):
            with patch("import_local_runtime.download") as download:
                with self.assertRaises(ValueError):
                    import_runtime(self.output, {**valid, **change})
                download.assert_not_called()

    def test_replaced_asset_and_inconsistent_local_report_are_rejected(self):
        pin = self.inputs()
        self.assets["capacity.json"] = b"changed after pinning"
        with patch("import_local_runtime.download", side_effect=self.download):
            with self.assertRaisesRegex(ValueError, "checksum"):
                import_runtime(self.output, pin)
        self.output.rename(self.output.with_name("tampered"))
        provenance = {**self.provenance, "runtime_key": "c" * 64}
        pin = self.inputs(provenance=provenance)
        with (
            patch("import_local_runtime.download", side_effect=self.download),
            patch("import_local_runtime.validate_capacity"),
            self.assertRaisesRegex(ValueError, "provenance disagrees"),
        ):
            import_runtime(self.output, pin)
        self.assertFalse((self.output / "binary").exists())

    def test_native_validation_failure_prevents_binary_download(self):
        pin = copy.deepcopy(self.inputs())
        with (
            patch("import_local_runtime.download", side_effect=self.download) as download,
            patch(
                "import_local_runtime.validate_capacity", side_effect=ValueError("native inputs")
            ),
            self.assertRaisesRegex(ValueError, "native inputs"),
        ):
            import_runtime(self.output, pin)
        self.assertEqual(download.call_count, 1)


if __name__ == "__main__":
    unittest.main()
