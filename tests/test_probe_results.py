"""Probe commands reject failed evidence, including with Python optimization."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_safe_storage
import check_shortcuts


class ProbeResults(unittest.TestCase):
    def test_storage_rejects_failure_or_wrong_relaunch_without_writing_success(self):
        for result in (
            {"passed": False, "reopened": False},
            {"passed": True, "reopened": True},
            {},
        ):
            with self.subTest(result=result), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                with (
                    patch.object(
                        sys,
                        "argv",
                        [
                            "check",
                            "--runtime",
                            str(root / "electron"),
                            "--output",
                            str(root / "report"),
                        ],
                    ),
                    patch.object(check_safe_storage.subprocess, "run"),
                    patch.object(check_safe_storage, "read_json", return_value=result),
                    patch.object(check_safe_storage, "write_json") as write,
                ):
                    with self.assertRaisesRegex(ValueError, "persistence"):
                        check_safe_storage.main()
                    write.assert_not_called()

    def test_shortcut_failure_or_wrong_identity_still_cleans_up_private_portal(self):
        for result, identity in (
            ({"passed": False}, "discord-canary"),
            ({"passed": True}, "wrong-app"),
        ):
            with (
                self.subTest(result=result, identity=identity),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                (root / "portal-ready").touch()
                with (
                    patch.object(
                        sys,
                        "argv",
                        [
                            "check",
                            "--runtime",
                            str(root / "electron"),
                            "--utils",
                            str(root / "utils.node"),
                            "--output",
                            str(root),
                            "--inside-private-bus",
                        ],
                    ),
                    patch.object(check_shortcuts.subprocess, "run"),
                    patch.object(check_shortcuts.subprocess, "Popen") as portal,
                    patch.object(
                        check_shortcuts, "read_json", side_effect=[result, {"app_id": identity}]
                    ),
                ):
                    with self.assertRaisesRegex(ValueError, "shortcut delivery"):
                        check_shortcuts.main()
                    portal.return_value.terminate.assert_called_once()
                    portal.return_value.wait.assert_called_once()
