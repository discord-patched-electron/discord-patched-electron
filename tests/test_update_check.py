"""Background preparation must leave the running installation untouched."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration/updater"))
import check as update_check


class UpdateCheck(unittest.TestCase):
    def test_background_prepares_and_foreground_activates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable, host = root / "runtime/electron", root / "prepared"
            with (
                patch.object(update_check, "ensure_runtime", return_value=executable),
                patch.object(update_check, "ensure_client", return_value=host),
                patch.object(update_check, "publish", return_value=root / "app-1.0.1") as publish,
                patch.object(
                    update_check,
                    "read_object",
                    return_value={"modules": {"discord_voice": {"version": 7}}},
                ),
                patch.dict(os.environ, {"DPE_PREPARE_ONLY": "1", "DPE_CLIENT_OFFLINE": "0"}),
            ):
                result = update_check.check(root / "app", root / "dpe-updater", "canary")
                self.assertEqual(result, {"runtime": str(executable), "host": str(host)})
                publish.assert_not_called()
                os.environ["DPE_PREPARE_ONLY"] = "0"
                result = update_check.check(root / "app", root / "dpe-updater", "canary")
                publish.assert_called_once_with(root / "app", root, host, executable)
                self.assertEqual(result["installed_runtime"], str(root / "app-1.0.1/electron"))
                self.assertEqual(
                    result["voice"], str(root / "app-1.0.1/modules/discord_voice-7/discord_voice")
                )

    def test_launch_uses_the_completed_result_and_rejects_unsafe_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "result"
            value = {
                "runtime": "/app/runtime/electron",
                "host": "/profile/prepared",
                "installed_runtime": "/profile/app-1.0.1/electron",
                "voice": "/profile/app-1.0.1/modules/voice",
            }
            path.write_text(json.dumps(value))
            with contextlib.redirect_stdout(io.StringIO()) as output:
                update_check.launch_paths(path)
            self.assertEqual(output.getvalue().splitlines(), list(value.values()))
            for bad in ("relative", "/path\ninjected", None):
                path.write_text(json.dumps({**value, "voice": bad}))
                with self.assertRaises(ValueError):
                    update_check.launch_paths(path)
