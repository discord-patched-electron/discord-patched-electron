from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from smoke_packages import missing_libraries


class NativeLibraries(unittest.TestCase):
    def test_dispatch_exception_cannot_hide_changed_binary_or_other_libraries(self):
        name = "discord_dispatch/discord_dispatch.node"
        checksum = "e5bb33a5c07fdc46f84db2e8fb9c37a3ba9154adeafc614bdc8673b160e180bd"
        listing = "\tlibssl.so.1.1 => not found\n\tlibcrypto.so.1.1 => not found\n"
        self.assertEqual(missing_libraries(name, checksum, listing), ["libcrypto.so.1.1", "libssl.so.1.1"])
        for path, digest, libraries in [
            (name, "0" * 64, listing),
            ("discord_voice/discord_voice.node", checksum, listing),
            (name, checksum, listing + "\tlibpulse.so.0 => not found\n")]:
            with self.assertRaisesRegex(ValueError, "Unresolved"):
                missing_libraries(path, digest, libraries)


if __name__ == "__main__":
    unittest.main()
