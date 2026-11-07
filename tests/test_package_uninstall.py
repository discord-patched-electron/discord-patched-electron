"""Exercise package cleanup in a private filesystem, never the host's KMS setup."""

import fcntl
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from package_release import removal_scripts

spec = importlib.util.spec_from_file_location(
    "dpe_uninstall", REPO / "integration/bootstrap/uninstall.py"
)
uninstall = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uninstall)
NAME = "discord-canary-prerelease-patched-electron-bin"
OTHER = "discord-stable-stable-patched-electron-bin"


class PackageUninstall(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        values = {
            "SYSTEM_ROOT": self.root,
            "ROOT_UID": os.getuid(),
            "KMS_ROOT": self.root / "usr/local/libexec/discord-patched-electron",
            "RULES": self.root / "etc/polkit-1/rules.d",
            "ACTIONS": self.root / "usr/share/polkit-1/actions",
            "APPARMOR": self.root / "etc/apparmor.d",
            "APPARMOR_PROFILES": self.root / "sys/kernel/security/apparmor/profiles",
            "APPARMOR_PARSER": self.root / "sbin/apparmor_parser",
            "INSTALL_BASES": (self.root / "opt", self.root / "usr/lib"),
        }
        mocked = patch.multiple(uninstall, **values)
        mocked.start()
        self.addCleanup(mocked.stop)
        for directory in (
            uninstall.KMS_ROOT,
            uninstall.RULES,
            uninstall.ACTIONS,
            *uninstall.INSTALL_BASES,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self.own = self.install(NAME)
        for name in uninstall.GLOBAL_FILES:
            (uninstall.KMS_ROOT / name).write_text("root helper fixture")
        (uninstall.ACTIONS / "org.discord-patched-electron.capture.policy").write_text("policy")
        self.profile = self.root / "home/test/discord/settings.json"
        self.profile.parent.mkdir(parents=True)
        self.profile.write_text("preserve my settings")

    def install(self, package, base=0):
        root = uninstall.INSTALL_BASES[base] / package
        root.mkdir(parents=True, exist_ok=True)
        (root / "bootstrap.json").write_text("{}")
        return root

    def grant(self, uid, channel="canary", track="prerelease"):
        for name in ("permissions", "maintenance"):
            (uninstall.KMS_ROOT / name).mkdir(exist_ok=True)
        marker = uninstall.KMS_ROOT / "permissions" / str(uid)
        marker.write_text("DPE-KMS-3\nfixture")
        (uninstall.KMS_ROOT / "maintenance" / str(uid)).write_text(
            json.dumps({"schema": 1, "channel": channel, "track": track})
        )
        (uninstall.RULES / f"50-discord-patched-electron-kms-{uid}.rules").write_text("grant")
        return marker

    def test_last_package_removes_all_users_helpers_rules_caches_and_interrupted_writes(self):
        self.grant(1000)
        self.grant(1001, "stable", "stable")
        cache = uninstall.KMS_ROOT / "__pycache__"
        cache.mkdir()
        (cache / "kms-permission-setup.cpython-314.pyc").write_text("cache")
        for directory in (
            uninstall.KMS_ROOT,
            uninstall.KMS_ROOT / "permissions",
            uninstall.KMS_ROOT / "maintenance",
            uninstall.RULES,
            uninstall.ACTIONS,
        ):
            (directory / ".dpe-kms-interrupted").write_text("partial atomic write")
        unrelated = uninstall.RULES / "10-other-app.rules"
        unrelated.write_text("other application")
        uninstall.cleanup(NAME, self.own)
        self.assertFalse(uninstall.KMS_ROOT.exists())
        self.assertEqual(list(uninstall.RULES.iterdir()), [unrelated])
        self.assertEqual(list(uninstall.ACTIONS.iterdir()), [])
        self.assertEqual(self.profile.read_text(), "preserve my settings")
        uninstall.cleanup(NAME, self.own)
        self.assertFalse(uninstall.KMS_ROOT.exists())

    def test_shared_install_keeps_helper_and_only_revokes_removed_package_users(self):
        self.install(OTHER)
        removed = self.grant(1000)
        kept = self.grant(1001, "stable", "stable")
        # Historical or malformed authority has no reliable package attribution.
        legacy = self.grant(1002)
        (uninstall.KMS_ROOT / "maintenance/1002").write_text("invalid historical record")
        uninstall.cleanup(NAME, self.own)
        self.assertFalse(removed.exists())
        self.assertTrue(kept.exists())
        self.assertTrue(legacy.exists())
        self.assertTrue((uninstall.KMS_ROOT / "kms-maintenance").exists())
        self.assertFalse((uninstall.RULES / "50-discord-patched-electron-kms-1000.rules").exists())
        self.assertTrue((uninstall.RULES / "50-discord-patched-electron-kms-1001.rules").exists())
        # Simulate the package manager completing the first removal.
        (self.own / "bootstrap.json").unlink()
        self.own.rmdir()
        uninstall.cleanup(OTHER, uninstall.INSTALL_BASES[0] / OTHER)
        self.assertFalse(uninstall.KMS_ROOT.exists())

    def test_same_package_in_other_managed_prefix_preserves_grants(self):
        self.install(NAME, base=1)
        marker = self.grant(1000)
        uninstall.cleanup(NAME, self.own)
        self.assertTrue(marker.exists())

    def test_portable_cleanup_preserves_managed_packages_then_cleans_final_state(self):
        marker = self.grant(1000)
        uninstall.cleanup(None, self.root / "portable")
        self.assertTrue(marker.exists())
        (self.own / "bootstrap.json").unlink()
        self.own.rmdir()
        uninstall.cleanup(None, self.root / "portable")
        self.assertFalse(uninstall.KMS_ROOT.exists())

    def test_stale_policy_and_rule_are_removed_when_helper_directory_is_absent(self):
        for path in uninstall.KMS_ROOT.iterdir():
            path.unlink()
        uninstall.KMS_ROOT.rmdir()
        stale = uninstall.RULES / "50-discord-patched-electron-kms-1003.rules"
        stale.write_text("stale grant")
        uninstall.cleanup(NAME, self.own)
        self.assertFalse(stale.exists())
        self.assertEqual(list(uninstall.ACTIONS.iterdir()), [])
        self.assertFalse(uninstall.KMS_ROOT.exists())

    def test_symlinks_and_unrelated_files_cannot_escape_cleanup(self):
        outside = self.root / "outside"
        outside.mkdir()
        sentinel = outside / "1000"
        sentinel.write_text("untouched")
        (uninstall.KMS_ROOT / "permissions").symlink_to(outside, target_is_directory=True)
        (uninstall.KMS_ROOT / "kms-server").unlink()
        (uninstall.KMS_ROOT / "kms-server").symlink_to(sentinel)
        (uninstall.KMS_ROOT / "unrelated-file").write_text("untouched")
        uninstall.cleanup(NAME, self.own)
        self.assertEqual(sentinel.read_text(), "untouched")
        self.assertEqual([p.name for p in uninstall.KMS_ROOT.iterdir()], ["unrelated-file"])

    def test_writable_system_directory_is_rejected_before_revocation(self):
        marker = self.grant(1000)
        uninstall.RULES.chmod(0o777)
        with self.assertRaisesRegex(ValueError, "Untrusted system directory"):
            uninstall.cleanup(NAME, self.own)
        self.assertTrue(marker.exists())

    def test_cleanup_holds_shared_lock_and_revokes_before_helper_removal(self):
        marker = self.grant(1000)
        unlink = Path.unlink
        removed = []

        def inspect(path, *args, **kwargs):
            if path == marker:
                fd = os.open(uninstall.KMS_ROOT / ".setup.lock", os.O_WRONLY)
                try:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(fd)
            removed.append(path)
            return unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", new=inspect):
            uninstall.cleanup(NAME, self.own)
        self.assertLess(removed.index(marker), removed.index(uninstall.KMS_ROOT / "kms-server"))
        self.assertLess(
            removed.index(marker),
            removed.index(uninstall.RULES / "50-discord-patched-electron-kms-1000.rules"),
        )

    def test_cli_requires_root_and_invalid_package_cannot_remove_anything(self):
        with (
            patch.object(uninstall.os, "geteuid", return_value=1000),
            patch.object(sys, "argv", ["uninstall.py"]),
        ):
            self.assertEqual(uninstall.main(), 1)
        with self.assertRaisesRegex(ValueError, "Invalid DPE package"):
            uninstall.cleanup("../../other", self.own)
        self.assertTrue((uninstall.KMS_ROOT / "kms-server").exists())

    def test_loaded_apparmor_profile_is_unloaded_and_removed_with_its_package(self):
        uninstall.APPARMOR.mkdir()
        profile = uninstall.APPARMOR / NAME
        profile.write_text("generated scoped DPE profile")
        uninstall.APPARMOR_PROFILES.parent.mkdir(parents=True)
        uninstall.APPARMOR_PROFILES.write_text(f"{NAME} (unconfined)\nunrelated (enforce)\n")
        unrelated = uninstall.APPARMOR / "unrelated"
        unrelated.write_text("preserve")
        with patch.object(uninstall.subprocess, "run") as parser:
            uninstall.cleanup(NAME, self.own)
            parser.assert_called_once_with(
                [str(uninstall.APPARMOR_PARSER), "-R", str(profile)], check=True
            )
        self.assertFalse(profile.exists())
        self.assertEqual(unrelated.read_text(), "preserve")
        with patch.object(uninstall.subprocess, "run") as parser:
            uninstall.cleanup(NAME, self.own)
            parser.assert_not_called()

    def test_shared_package_preserves_other_apparmor_profiles_and_unloaded_needs_no_parser(self):
        self.install(OTHER)
        uninstall.APPARMOR.mkdir()
        removed = uninstall.APPARMOR / NAME
        removed.write_text("generated profile")
        kept = uninstall.APPARMOR / OTHER
        kept.write_text("other DPE profile")
        with patch.object(uninstall.subprocess, "run") as parser:
            uninstall.cleanup(NAME, self.own)
            parser.assert_not_called()
        self.assertFalse(removed.exists())
        self.assertTrue(kept.exists())

    def test_native_scriptlet_guards_skip_upgrades_and_run_final_removal(self):
        scripts = removal_scripts(self.root, NAME)
        python = self.root / "python3"
        python.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$DPE_UNINSTALL_LOG"\n')
        python.chmod(0o755)
        log = self.root / "called"
        for kind, remove, skip in (
            ("deb", "remove", ("upgrade", "deconfigure", "failed-upgrade")),
            ("rpm", "0", ("1", "2")),
        ):
            script = scripts[kind]
            environment = {
                **os.environ,
                "PATH": f"{self.root}:/usr/bin",
                "DPE_UNINSTALL_LOG": str(log),
            }
            for argument in (*skip, ""):
                subprocess.run(["sh", str(script), argument], env=environment, check=True)
                self.assertFalse(log.exists())
            subprocess.run(["sh", str(script), remove], env=environment, check=True)
            self.assertEqual(
                log.read_text().splitlines(), ["-I", f"/opt/{NAME}/uninstall.py", "--package", NAME]
            )
            log.unlink()


if __name__ == "__main__":
    unittest.main()
