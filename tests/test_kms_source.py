import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_rust_support import build as build_rust


def rust_link():
    archive, _ = build_rust("dpe-kms")
    return [str(archive), "-ldl", "-lpthread", "-lm", "-Wl,--gc-sections"]


@unittest.skipUnless(shutil.which("cc") and shutil.which("pkg-config"), "C toolchain required")
class KmsSource(unittest.TestCase):
    def test_privileged_server_refuses_unprivileged_execution(self):
        # Boundary/DRM ownership fixtures live in the Rust crate. This checks
        # the actual production executable, without auth or GPU access.
        rust_link()
        target = ROOT / "out/rust-target/release/dpe-kms-server"
        result = subprocess.run([target], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_startup_authorization_and_cancellation(self):
        flags = subprocess.check_output(
            ["pkg-config", "--cflags", "--libs", "libdrm", "libcap"], text=True
        ).split()
        wrappers = [
            "lstat",
            "access",
            "fopen",
            "prctl",
            "getsockopt",
            "kill",
            "posix_spawn",
        ]
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "kms-startup-check"
            subprocess.run(
                [
                    "cc",
                    "-std=c11",
                    "-O1",
                    "-g",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-fsanitize=address,undefined",
                    "-fno-omit-frame-pointer",
                    str(ROOT / "tests/screenshare/kms-startup-check.c"),
                    *(f"-Wl,--wrap={symbol}" for symbol in wrappers),
                    *rust_link(),
                    *flags,
                    "-o",
                    str(target),
                ],
                check=True,
            )
            result = subprocess.run([target], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("peer authentication and cancellation passed", result.stdout)

    def test_protocol_selection_and_fd_ownership(self):
        flags = subprocess.check_output(
            ["pkg-config", "--cflags", "--libs", "libdrm", "libcap"], text=True
        ).split()
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "kms-client-check"
            subprocess.run(
                [
                    "cc",
                    "-std=c11",
                    "-O1",
                    "-g",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-fsanitize=address,undefined",
                    "-fno-omit-frame-pointer",
                    str(ROOT / "tests/screenshare/kms-client-check.c"),
                    *rust_link(),
                    *flags,
                    "-o",
                    str(target),
                ],
                check=True,
            )
            result = subprocess.run([target], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("descriptor ownership passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
