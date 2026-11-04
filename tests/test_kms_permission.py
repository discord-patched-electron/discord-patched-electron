import hashlib
import importlib.util
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "kms_permission_setup", ROOT / "integration/video/kms-permission-setup.py"
)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class KmsPermission(unittest.TestCase):
    def test_policy_binds_dedicated_action_to_only_bundled_helper(self):
        self.assertIn('<action id="org.discord-patched-electron.capture">', setup.POLICY)
        self.assertIn(
            'key="org.freedesktop.policykit.exec.path">/usr/local/libexec/discord-patched-electron/kms-server</annotate>',
            setup.POLICY,
        )
        self.assertIn("<allow_active>no</allow_active>", setup.POLICY)
        rule = setup.rule('quoted"user')
        self.assertIn('action.id === "org.discord-patched-electron.capture"', rule)
        self.assertIn('subject.user === "quoted\\"user"', rule)
        self.assertIn("subject.local && subject.active", rule)
        self.assertNotIn("action.lookup", rule)
        self.assertNotIn("gsr-kms-server", setup.POLICY + rule)

    def test_installer_rejects_writable_existing_ancestors(self):
        def info(path):
            return os.stat_result(
                (
                    stat.S_IFDIR | (0o777 if path == Path("/usr/local") else 0o755),
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                )
            )

        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "lstat", autospec=True, side_effect=info),
            patch.object(Path, "mkdir") as mkdir,
        ):
            with self.assertRaises(ValueError):
                setup.root_directory(setup.ROOT)
            mkdir.assert_not_called()

    def test_bundled_payload_checks_exact_manifest_and_rejects_links(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(setup, "BUNDLE", Path(temporary)),
        ):
            bundle = Path(temporary)
            data = b"\x7fELF" + b"fixture" * 32
            (bundle / "dpe-kms-server").write_bytes(data)
            (bundle / "kms-server.json").write_text(
                json.dumps(
                    {
                        "protocol": 6,
                        "permissionVersion": 2,
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                )
            )
            self.assertEqual(setup.read_payload(), data)
            (bundle / "dpe-kms-server").write_bytes(data + b"changed")
            with self.assertRaises(ValueError):
                setup.read_payload()
            (bundle / "dpe-kms-server").unlink()
            (bundle / "target").write_bytes(data)
            (bundle / "dpe-kms-server").symlink_to(bundle / "target")
            with self.assertRaises(OSError):
                setup.read_payload()

    def test_revoke_removes_only_invoking_account_and_marker_first(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root, rules = base / "root", base / "rules"
            (root / "permissions").mkdir(parents=True)
            rules.mkdir()
            for uid in (1000, 1001):
                (root / "permissions" / str(uid)).write_bytes(setup.MARKER)
                (rules / f"50-discord-patched-electron-kms-{uid}.rules").write_text("fixture")
            with (
                patch.object(setup, "ROOT", root),
                patch.object(setup, "RULES", rules),
                patch.object(setup, "root_directory"),
                patch.object(
                    setup.pwd, "getpwuid", return_value=type("User", (), {"pw_name": "fixture"})()
                ),
                patch.object(
                    setup.os,
                    "fstat",
                    return_value=os.stat_result((stat.S_IFREG | 0o600, 0, 0, 0, 0, 0, 0, 0, 0, 0)),
                ),
                patch.object(setup, "read_payload") as read,
            ):
                setup.change(1000, False)
                read.assert_not_called()
            self.assertFalse((root / "permissions/1000").exists())
            self.assertFalse((rules / "50-discord-patched-electron-kms-1000.rules").exists())
            self.assertTrue((root / "permissions/1001").exists())
            self.assertTrue((rules / "50-discord-patched-electron-kms-1001.rules").exists())

    def test_installer_requires_pkexec_identity_and_fixed_modes(self):
        with (
            patch.object(setup, "change") as change,
            patch.object(setup.os, "geteuid", return_value=0),
        ):
            for identity in ("", "0", "1000;bad", "-1"):
                with (
                    patch.dict(os.environ, {"PKEXEC_UID": identity}),
                    patch.object(setup.sys, "argv", ["setup", "--allow"]),
                ):
                    self.assertEqual(setup.main(), 1)
            with (
                patch.dict(os.environ, {"PKEXEC_UID": "1000"}),
                patch.object(setup.sys, "argv", ["setup", "--allow", "/untrusted/path"]),
            ):
                self.assertEqual(setup.main(), 1)
            change.assert_not_called()


if __name__ == "__main__":
    unittest.main()
