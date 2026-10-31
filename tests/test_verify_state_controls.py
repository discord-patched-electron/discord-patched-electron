"""Independent offline controls; synthetic payloads never cross an exec boundary."""

import os
import unittest
from unittest.mock import patch

import test_application_updates as app_fixture
import test_repro_state_reliability as supplied
import test_runtime_updater as runtime_fixture

client = runtime_fixture.client
runtime = runtime_fixture.runtime
loader = app_fixture.loader


class CacheControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        runtime_fixture.RuntimeUpdates.setUpClass()

    @classmethod
    def tearDownClass(cls):
        runtime_fixture.RuntimeUpdates.tearDownClass()

    def setUp(self):
        self.fixture = runtime_fixture.RuntimeUpdates()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()

    def test_offline_recovery_rechecks_pins_compatibility_and_module_integrity(self):
        with (
            self.fixture.modules() as (root, _installed),
            patch.dict(os.environ, {"DPE_CLIENT_PINNED": "1"}),
            patch.object(runtime, "download", side_effect=AssertionError("network forbidden")),
        ):
            host = client.ensure_client(root, "canary")
            pointer = root / "current.json"
            pointer.unlink()
            with (
                patch.object(client, "install_module", side_effect=AssertionError("no install")),
                patch.object(client, "prerequisites", side_effect=AssertionError("no patching")),
            ):
                with patch.object(client, "compatibility_key", return_value="e" * 64):
                    with self.assertRaises(ValueError):
                        client.ensure_client(root, "canary", offline=True)
                module = host / "modules/discord_voice/index.js"
                original = module.read_bytes()
                module.write_bytes(b"damaged")
                with self.assertRaises(ValueError):
                    client.ensure_client(root, "canary", offline=True)
                module.write_bytes(original)
                pins_file = self.fixture.app / "client-update.json"
                original_pins = pins_file.read_bytes()
                pins = client.read_object(pins_file)
                old_hash = pins["modules"]["discord_voice"]["full"]["package_sha256"]
                pins["modules"]["discord_voice"]["full"]["package_sha256"] = (
                    "b" if old_hash != "b" * 64 else "c"
                ) * 64
                client.write_json(pins_file, pins)
                with self.assertRaises(ValueError):
                    client.ensure_client(root, "canary", offline=True)
                self.assertFalse(pointer.exists())
                pins_file.write_bytes(original_pins)
                pointer.write_text("{")
                self.assertEqual(client.ensure_client(root, "canary", offline=True), host)
                self.assertEqual(client.current_host(root, "canary"), host)

    def test_s1_normal_mode_recovers_even_when_approval_network_is_unavailable(self):
        with self.fixture.modules() as (root, _installed):
            write = client.write_json

            def interrupt(path, value):
                if path == root / "current.json":
                    raise supplied.PublicationInterrupted
                write(path, value)

            with (
                patch.dict(os.environ, {"DPE_CLIENT_PINNED": "1"}),
                patch.object(client, "write_json", side_effect=interrupt),
                self.assertRaises(supplied.PublicationInterrupted),
            ):
                client.ensure_client(root, "canary")
            host = next((root / "generations").iterdir())
            with (
                patch.dict(os.environ, {"DPE_CLIENT_PINNED": "0"}),
                patch.object(runtime, "download", side_effect=OSError("synthetic offline")) as net,
                patch.object(client, "install_module", side_effect=AssertionError("no install")),
                patch.object(client, "prerequisites", side_effect=AssertionError("no patching")),
            ):
                self.assertEqual(client.ensure_client(root, "canary", offline=True), host)
                net.assert_not_called()
                self.assertEqual(client.ensure_client(root, "canary"), host)
                self.assertGreater(net.call_count, 0)
                self.assertEqual(client.ensure_client(root, "canary", offline=True), host)

    def test_s2_normal_mode_repairs_permission_damage(self):
        fixture = self.fixture
        fixture.updater_only_package()
        with patch.object(runtime, "probe"):
            executable = runtime.ensure_runtime(
                fixture.app, fixture.profile, offline=True, archive=fixture.archive
            )
        before = runtime.checksum(executable)
        executable.chmod(0o600)
        with (
            patch.object(runtime, "probe") as probe,
            patch.object(runtime, "download", side_effect=fixture.download) as download,
            patch.dict(os.environ, {"DPE_RUNTIME_UPDATES": "0"}),
        ):
            self.assertEqual(runtime.ensure_runtime(fixture.app, fixture.profile), executable)
            self.assertTrue(os.access(executable, os.X_OK))
            self.assertEqual(runtime.checksum(executable), before)
            probe.assert_called_once()
            download.assert_called_once()
            self.assertEqual(
                runtime_fixture.bootstrap.prepare(fixture.app, fixture.profile), str(executable)
            )


class InstanceControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app_fixture.ApplicationUpdates.setUpClass()

    @classmethod
    def tearDownClass(cls):
        app_fixture.ApplicationUpdates.tearDownClass()

    def test_trial_peer_registration_and_malformed_instance_recovery(self):
        fixture = app_fixture.ApplicationUpdates()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        alive = {22001: "100", 22002: "200"}
        with patch.object(loader, "process_identity", side_effect=alive.get):
            with patch.object(loader.os, "getpid", return_value=22001):
                app, token = loader.select(
                    fixture.root, fixture.package, fixture.policy, launch=True
                )
            with patch.object(loader.os, "getpid", return_value=22002):
                self.assertEqual(
                    loader.select(fixture.root, fixture.package, fixture.policy, launch=True),
                    (app, None),
                )
            state = loader.read_state(fixture.root)
            self.assertEqual(state["trial"]["token"], token)
            self.assertEqual(state["running"]["pid"], 22001)
            self.assertEqual(state["instances"][0]["pid"], 22002)
        for instances in (None, [{}], [dict(state["running"], process="invalid")]):
            with self.subTest(instances=instances):
                loader.write(fixture.root / "state.json", {**state, "instances": instances})
                self.assertEqual(loader.read_state(fixture.root), {})

    def test_s3_real_stage_preserves_survivors_and_prunes_reused_pids(self):
        fixture = app_fixture.ApplicationUpdates()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        alive = {21001: "100", 21002: "200", 21003: "300"}

        def select(pid):
            with (
                patch.object(loader.os, "getpid", return_value=pid),
                patch.object(loader, "process_identity", side_effect=alive.get),
            ):
                return loader.select(fixture.root, fixture.package, fixture.policy, launch=True)

        first, token = select(21001)
        (fixture.root / ("ready-" + token)).write_text("ready")
        self.assertEqual(select(21002), (first, None))
        candidate, archive = fixture.payload("second", "20261027.130000")
        # Use the real loader --stage path, rather than assigning pending by hand.
        staged = fixture.stage(candidate, archive)
        second = first.parent.parent / candidate["archive_sha256"] / "app"
        self.assertEqual(staged.stdout.strip(), str(second))
        before = loader.read_state(fixture.root)
        self.assertEqual(before["running"]["pid"], 21001)
        self.assertNotIn("trial", before)
        self.assertEqual(select(21003), (first, None))
        self.assertEqual(loader.read_state(fixture.root)["pending"], candidate["archive_sha256"])
        del alive[21001]
        self.assertEqual(select(21003), (first, None))
        self.assertEqual(loader.read_state(fixture.root)["running"]["pid"], 21002)
        # A reused PID must not pin the old generation indefinitely.
        alive[21002] = "201"
        selected, token = select(21003)
        self.assertEqual(selected, second)
        self.assertIsNotNone(token)
        self.assertEqual(loader.read_state(fixture.root)["running"]["pid"], 21003)
        self.assertEqual(loader.read_state(fixture.root)["instances"], [])
        # Old immutable code is retained; this test establishes no destructive effect.
        self.assertEqual(
            loader.generation(fixture.root, before["current"], fixture.package, fixture.policy)[0],
            first,
        )


if __name__ == "__main__":
    unittest.main()
