"""Real OpenPGP signatures reject modified packages, checksum lists and keys."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from release_signing import sign, verify


@unittest.skipUnless(shutil.which("gpg") and shutil.which("gpgv"), "GnuPG required")
class ReleaseSigning(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="signing-fixture-")
        cls.home = Path(cls.temporary.name)
        command = ["gpg", "--batch", "--homedir", str(cls.home)]
        subprocess.run(
            [
                *command,
                "--pinentry-mode",
                "loopback",
                "--passphrase",
                "",
                "--quick-generate-key",
                "Disposable signing fixture",
                "ed25519",
                "sign",
                "1d",
            ],
            check=True,
            capture_output=True,
        )
        cls.secret = subprocess.check_output([*command, "--armor", "--export-secret-keys"])
        cls.public = cls.home / "public.asc"
        cls.public.write_bytes(subprocess.check_output([*command, "--armor", "--export"]))

    @classmethod
    def tearDownClass(cls):
        subprocess.run(["gpgconf", "--homedir", str(cls.home), "--kill", "gpg-agent"], check=True)
        cls.temporary.cleanup()

    def test_sign_verify_retry_and_reject_tampering(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("release_signing.PUBLIC_KEY", self.public),
        ):
            directory = Path(temporary)
            (directory / "release.json").write_text(
                json.dumps({"assets": {"app.pkg.tar.zst": "unused"}})
            )
            (directory / "app.pkg.tar.zst").write_bytes(b"package bytes")
            (directory / "SHA256SUMS").write_text("checksum fixture\n")
            sign(directory, self.secret)
            verify(directory)
            before = (directory / "app.pkg.tar.zst.sig").read_bytes()
            sign(directory, self.secret)
            self.assertEqual(before, (directory / "app.pkg.tar.zst.sig").read_bytes())
            for name in ("app.pkg.tar.zst", "SHA256SUMS", "release-signing-key.asc"):
                path = directory / name
                original = path.read_bytes()
                path.write_bytes(b"changed")
                with (
                    self.subTest(name=name),
                    self.assertRaises((ValueError, subprocess.CalledProcessError)),
                ):
                    verify(directory)
                path.write_bytes(original)
            (directory / "SHA256SUMS.sig").unlink()
            with self.assertRaisesRegex(ValueError, "missing"):
                verify(directory, required=False)

    def test_unsigned_legacy_is_allowed_only_when_explicitly_requested(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "release.json").write_text(
                json.dumps({"assets": {"app.pkg.tar.zst": "unused"}})
            )
            verify(directory, required=False)
            with self.assertRaisesRegex(ValueError, "missing"):
                verify(directory)
