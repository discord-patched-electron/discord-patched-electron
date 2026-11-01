"""Run inside the built AppImage, including on a minimal offline distro."""

import ctypes
import json
import os
import pathlib
import shutil
import ssl
import subprocess
import sys
import tempfile

root = pathlib.Path(os.environ["DPE_TOOLS_DIR"]).parent.parent
assert pathlib.Path(sys.executable).resolve().is_relative_to(root)
assert sys.version_info >= (3, 11)
assert ssl.create_default_context().get_ca_certs()
for name in ("gpg", "gpgv", "brotli", "pactl", "pw-dump", "pw-cli", "pw-cat", "pw-loopback"):
    executable = shutil.which(name)
    assert pathlib.Path(executable).resolve().is_relative_to(root), (name, executable)
    subprocess.run([executable, "--version"], check=True, stdout=subprocess.DEVNULL)
for library in ("libgtk-3.so.0", "libnss3.so", "libsoftokn3.so", "libpulse.so.0", "libasound.so.2"):
    ctypes.CDLL(str(root / "usr/lib" / library))
compressed = subprocess.check_output(["brotli", "--stdout"], input=b"dependency test")
assert (
    subprocess.check_output(["brotli", "--decompress", "--stdout"], input=compressed)
    == b"dependency test"
)
# Exercise the exact public signature path used by application/runtime updates,
# without personal keys, network access or a host GnuPG installation.
fixture = pathlib.Path(__file__).parent
with tempfile.TemporaryDirectory() as temporary:
    keyring = pathlib.Path(temporary) / "release.gpg"
    subprocess.run(
        [
            "gpg",
            "--batch",
            "--homedir",
            temporary,
            "--dearmor",
            "--output",
            str(keyring),
            str(fixture / "release-signing-key.asc"),
        ],
        check=True,
    )
    subprocess.run(
        [
            "gpgv",
            "--homedir",
            temporary,
            "--keyring",
            str(keyring),
            str(fixture / "SHA256SUMS.sig"),
            str(fixture / "SHA256SUMS"),
        ],
        check=True,
    )
print(json.dumps(sys.argv[1:]))
sys.exit(23)
