"""Exercise the compiler preflight entry point without allocating a compiler."""

import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_reference import build_config
from common import ROOT, read_json
from runtime_fingerprints import runtime_key


class RuntimePreflight(unittest.TestCase):
    def test_locked_sources_pass_and_changed_sources_fail(self):
        sources = read_json(ROOT / "config/sources.lock.json")
        key = runtime_key(sources["targets"]["prerelease"], sources, build_config("prerelease"))
        for expected, succeeds in ((key, True), ("0" * 64, False)):
            with self.subTest(expected=expected):
                result = subprocess.run(
                    [sys.executable, str(ROOT / "scripts/check_runtime_key.py")],
                    cwd="/tmp",
                    env={
                        **os.environ,
                        "ELECTRON_BUILD_TARGET": "prerelease",
                        "EXPECTED_RUNTIME_KEY": expected,
                    },
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode == 0, succeeds, result.stderr)
                if not succeeds:
                    self.assertIn("Source changed since scheduling", result.stderr)
