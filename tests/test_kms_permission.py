import hashlib
import importlib.util
import json
import os
import stat
import tempfile
import unittest
from contextlib import nullcontext
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
                        "permissionVersion": 4,
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

    def test_filesystem_identity_restores_nested_contexts_and_errors(self):
        identity = 0
        transitions = []

        def switch(uid):
            nonlocal identity
            previous = identity
            if uid != setup._QUERY_FSUID:
                transitions.append(uid)
                identity = uid
            return previous

        with (
            patch.object(setup, "_SETFSUID", side_effect=switch),
            patch.object(os, "seteuid") as euid,
        ):
            with self.assertRaisesRegex(ValueError, "stop"):
                with setup.as_user(1000):
                    with setup.as_user(1000):
                        self.assertEqual(identity, 1000)
                        raise ValueError("stop")
            self.assertEqual(identity, 0)
            self.assertEqual(transitions, [1000, 1000, 1000, 0])
            euid.assert_not_called()

    def test_filesystem_identity_rejects_a_silent_switch_failure(self):
        with patch.object(setup, "_SETFSUID", return_value=0):
            with self.assertRaises(PermissionError):
                with setup.as_user(1000):
                    self.fail("Unverified filesystem identity was accepted")

    def test_executable_binding_uses_the_live_inode_and_rejects_another_owner(self):
        binding = setup.executable_grant(os.getuid(), os.getpid())
        self.assertTrue(binding.startswith(b"DPE-KMS-3\n"))
        lines = binding.decode().splitlines()
        self.assertEqual(lines[-1], os.readlink("/proc/self/exe"))
        self.assertEqual(int(lines[2]), Path("/proc/self/exe").stat().st_ino)
        with self.assertRaises(ValueError):
            setup.executable_grant(os.getuid() + 1, os.getpid())

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
                patch.object(setup, "as_user", return_value=nullcontext()) as identity,
            ):
                setup.change(1000, False)
                read.assert_not_called()
                identity.assert_called_once_with(1000)
            self.assertFalse((root / "permissions/1000").exists())
            self.assertFalse((rules / "50-discord-patched-electron-kms-1000.rules").exists())
            self.assertTrue((root / "permissions/1001").exists())
            self.assertTrue((rules / "50-discord-patched-electron-kms-1001.rules").exists())

    def test_install_retires_old_grant_before_server_and_publishes_binding_last(self):
        for fail in (False, True):
            with tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                root, rules, actions = base / "root", base / "rules", base / "actions"
                (root / "permissions").mkdir(parents=True)
                rules.mkdir()
                actions.mkdir()
                marker = root / "permissions/1000"
                marker.write_bytes(b"DPE-KMS-2\n")
                binding = b"DPE-KMS-3\n1\n2\n3\n4\n5\n/current/discord\n"
                writes = []
                original = setup.atomic_write

                def write(
                    path,
                    data,
                    mode,
                    *,
                    writes=writes,
                    marker=marker,
                    fail=fail,
                    actions=actions,
                    original=original,
                ):
                    writes.append(path)
                    self.assertFalse(marker.exists())
                    if fail and path.parent == actions:
                        raise OSError("interrupted install")
                    original(path, data, mode)

                with (
                    patch.object(setup, "ROOT", root),
                    patch.object(setup, "SERVER", root / "kms-server"),
                    patch.object(setup, "RULES", rules),
                    patch.object(setup, "ACTIONS", actions),
                    patch.object(setup, "root_directory"),
                    patch.object(setup, "read_payload", return_value=b"ELF fixture"),
                    patch.object(setup, "executable_grant", return_value=binding),
                    patch.object(setup, "atomic_write", side_effect=write),
                    patch.object(setup, "bootstrap_maintenance", return_value=None),
                    patch.object(setup, "as_user", return_value=nullcontext()) as identity,
                    patch.object(
                        setup.pwd,
                        "getpwuid",
                        return_value=type("User", (), {"pw_name": "fixture"})(),
                    ),
                    patch.object(
                        setup.os,
                        "fstat",
                        return_value=os.stat_result(
                            (stat.S_IFREG | 0o600, 0, 0, 0, 0, 0, 0, 0, 0, 0)
                        ),
                    ),
                ):
                    if fail:
                        with self.assertRaises(OSError):
                            setup.change(1000, True, 123)
                    else:
                        setup.change(1000, True, 123)
                    identity.assert_called_once_with(1000)
                if fail:
                    self.assertFalse(marker.exists())
                else:
                    self.assertEqual(marker.read_bytes(), binding)
                    self.assertEqual(writes[-1], marker)

    def test_initial_authority_installs_fixed_root_files_and_denies_direct_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rules, actions = root / "rules", root / "actions"
            for directory in (rules, actions, root / "permissions", root / "maintenance"):
                directory.mkdir()
            policy = {
                "schema": 1,
                "enabled": True,
                "release_version": None,
                "current": {
                    role: "a" * 64 for role in ("runtime", "server", "maintainer", "installer")
                },
            }
            with (
                patch.object(setup, "ROOT", root),
                patch.object(setup, "SERVER", root / "kms-server"),
                patch.object(setup, "RULES", rules),
                patch.object(setup, "ACTIONS", actions),
                patch.object(
                    setup.pwd, "getpwuid", return_value=type("User", (), {"pw_name": "fixture"})()
                ),
            ):
                setup.apply(
                    1000,
                    b"fixture",
                    b"binding",
                    maintenance=(b"manager", b"installer", policy, b"public key"),
                )
                self.assertEqual((root / "kms-server").stat().st_mode & 0o777, 0o744)
                self.assertEqual((root / "kms-maintenance").stat().st_mode & 0o777, 0o700)
                self.assertEqual((root / "kms-permission-setup.py").stat().st_mode & 0o777, 0o600)
                self.assertEqual(json.loads((root / "maintenance/1000").read_text()), policy)
                self.assertIn(
                    "org.discord-patched-electron.capture.maintain",
                    (rules / "50-discord-patched-electron-kms-1000.rules").read_text(),
                )
                setup.apply(1000, None, None)
                self.assertFalse(json.loads((root / "maintenance/1000").read_text())["enabled"])
                self.assertFalse((root / "permissions/1000").exists())

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
