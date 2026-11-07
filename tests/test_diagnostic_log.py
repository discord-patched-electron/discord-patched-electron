"""Local diagnostic persistence must not change protocol output or launch failures."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration/updater"))
import importlib.util

import diagnostic_log
import support


class DiagnosticLogs(unittest.TestCase):
    def test_updater_progress_persists_when_protocol_output_is_disabled(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(os.environ, {"DPE_PROFILE_DIR": temporary, "DPE_UPDATE_PROGRESS": "0"}),
        ):
            support.progress("download", "Discord voice", current=1, total=2)
            file = Path(temporary) / "logs/dpe/updater.log"
            record = json.loads(file.read_text())
            self.assertEqual(record["stage"], "download")
            self.assertEqual(record["label"], "Discord voice")
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)

    def test_relay_forwards_exact_output_and_retains_complete_lines(self):
        with tempfile.TemporaryDirectory() as temporary:
            payload = b"first line\nsecond line without newline"
            result = subprocess.run(
                [sys.executable, str(Path(diagnostic_log.__file__)), temporary, "stderr"],
                input=payload,
                capture_output=True,
                check=True,
            )
            self.assertEqual(result.stdout, payload)
            file = Path(temporary) / "logs/dpe/launcher-stderr.log"
            self.assertEqual(file.read_bytes(), payload + b"\n")

    def test_rotation_is_bounded_and_links_are_not_followed(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(diagnostic_log, "LIMIT", 8):
            for number in range(8):
                diagnostic_log.append(temporary, "test.log", f"record {number}\n")
            directory = Path(temporary) / "logs/dpe"
            self.assertEqual(
                len(list(directory.glob("test.log*"))), 4
            )  # live + two rotations + lock
            other = Path(temporary) / "other"
            other.write_text("unchanged")
            (directory / "linked.log").symlink_to(other)
            diagnostic_log.append(temporary, "linked.log", "must not overwrite")
            self.assertEqual(other.read_text(), "unchanged")

    def test_oversized_line_is_omitted_instead_of_retaining_partial_secrets(self):
        with tempfile.TemporaryDirectory() as temporary:
            payload = b"x" * 70000 + b"\nnormal error\n"
            result = subprocess.run(
                [sys.executable, str(Path(diagnostic_log.__file__)), temporary, "stdout"],
                input=payload,
                capture_output=True,
                check=True,
            )
            self.assertEqual(result.stdout, payload)
            text = (Path(temporary) / "logs/dpe/launcher-stdout.log").read_text()
            self.assertEqual(text, "Oversized log line omitted\nnormal error\n")

    def test_installed_bootstrap_diagnostics_are_bounded_and_failure_isolated(self):
        loader_file = Path(__file__).resolve().parents[1] / "integration/bootstrap/loader.py"
        spec = importlib.util.spec_from_file_location("diagnostic_loader_fixture", loader_file)
        loader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(loader)
        with tempfile.TemporaryDirectory() as temporary:
            loader._diagnostic_profile = Path(temporary)
            loader.diagnostic("bootstrap-error", error="synthetic failed activation")
            record = json.loads((Path(temporary) / "logs/dpe/bootstrap-loader.log").read_text())
            self.assertEqual(record["stage"], "bootstrap-error")
            loader._diagnostic_profile = Path("/dev/null/not-a-directory")
            loader.diagnostic("must-not-break-launch")
