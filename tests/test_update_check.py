"""Background preparation must leave the running installation untouched."""

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
                patch.object(update_check, "prerequisites"),
                patch.object(update_check, "ensure_runtime", return_value=executable),
                patch.object(update_check, "ensure_client", return_value=host),
                patch.object(update_check, "publish") as publish,
                patch.dict(os.environ, {"DPE_PREPARE_ONLY": "1", "DPE_CLIENT_OFFLINE": "0"}),
            ):
                result = update_check.check(root / "app", root / "dpe-updater", "canary")
                self.assertEqual(result, {"runtime": str(executable), "host": str(host)})
                publish.assert_not_called()
                os.environ["DPE_PREPARE_ONLY"] = "0"
                update_check.check(root / "app", root / "dpe-updater", "canary")
                publish.assert_called_once_with(root / "app", root, host, executable)
