import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("cc") and shutil.which("pkg-config"), "C toolchain required")
class KmsSource(unittest.TestCase):
    def test_privileged_server_boundary_without_privileges_or_capture(self):
        flags = subprocess.check_output(
            ["pkg-config", "--cflags", "--libs", "libdrm"], text=True
        ).split()
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "kms-server-check"
            subprocess.run(
                [
                    "cc",
                    "-std=c11",
                    "-O1",
                    "-g",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-Wno-unused-function",
                    "-fsanitize=address,undefined",
                    "-fno-omit-frame-pointer",
                    str(ROOT / "tests/screenshare/kms-server-check.c"),
                    *flags,
                    "-o",
                    str(target),
                ],
                check=True,
            )
            result = subprocess.run([target], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("descriptor cleanup passed", result.stdout)

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
            "execve",
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
                    str(ROOT / "integration/video/kms-client.c"),
                    *(f"-Wl,--wrap={symbol}" for symbol in wrappers),
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
                    str(ROOT / "integration/video/kms-client.c"),
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
