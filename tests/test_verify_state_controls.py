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
                with self.assertRaisesRegex(ValueError, "First launch requires an internet"):
                    client.ensure_client(root, "canary", offline=True)
                net.assert_not_called()
                self.assertEqual(client.ensure_client(root, "canary"), host)
                self.assertGreater(net.call_count, 0)
                self.assertEqual(client.ensure_client(root, "canary", offline=True), host)

    def test_s2_probe_is_not_reached_and_local_archive_does_not_force_repair(self):
        fixture = self.fixture
        fixture.updater_only_package()
        with patch.object(runtime, "probe"):
            executable = runtime.ensure_runtime(
                fixture.app, fixture.profile, offline=True, archive=fixture.archive
            )
        before = runtime.checksum(executable)
        executable.chmod(0o600)
        self.assertEqual(runtime.checksum(executable), before)
        with (
            patch.object(runtime, "probe", side_effect=AssertionError("must not execute")) as probe,
            patch.object(
                runtime, "install_runtime", side_effect=AssertionError("repair")
            ) as repair,
            patch.object(runtime, "download", side_effect=AssertionError("network")),
            patch.object(runtime, "latest", return_value=fixture.policy),
            patch.dict(os.environ, {"DPE_RUNTIME_UPDATES": "1"}),
        ):
            for options in ({}, {"offline": True, "archive": fixture.archive}):
                with self.subTest(options=options):
                    self.assertEqual(
                        runtime.ensure_runtime(fixture.app, fixture.profile, **options), executable
                    )
                    self.assertFalse(os.access(executable, os.X_OK))
            self.assertEqual(
                runtime_fixture.bootstrap.prepare(fixture.app, fixture.profile), str(executable)
            )
            probe.assert_not_called()
            repair.assert_not_called()
            executable.chmod(0o700)
            self.assertTrue(os.access(executable, os.X_OK))
            self.assertEqual(
                runtime.ensure_runtime(fixture.app, fixture.profile, offline=True), executable
            )


class InstanceControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app_fixture.ApplicationUpdates.setUpClass()

    @classmethod
    def tearDownClass(cls):
        app_fixture.ApplicationUpdates.tearDownClass()

    def test_s3_real_stage_preserves_live_owner_but_cannot_track_survivor(self):
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
        self.assertEqual(loader.read_state(fixture.root), before)
        del alive[21002]
        self.assertEqual(select(21003), (first, None))
        alive[21002] = "200"
        del alive[21001]
        selected, token = select(21003)
        self.assertEqual(selected, second)
        self.assertIsNotNone(token)
        self.assertEqual(alive[21002], "200")
        self.assertEqual(loader.read_state(fixture.root)["running"]["pid"], 21003)
        # Old immutable code is retained; this test establishes no destructive effect.
        self.assertEqual(
            loader.generation(fixture.root, before["current"], fixture.package, fixture.policy)[0],
            first,
        )


if __name__ == "__main__":
    unittest.main()
