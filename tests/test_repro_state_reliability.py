"""Regression tests for reviewed updater state transitions.

Only existing synthetic fixtures are used. Runtime probes and application exec
are mocked; no downloaded or extracted application/runtime code is executed.
"""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

import test_application_updates as application_fixture
import test_runtime_updater as runtime_fixture

client = runtime_fixture.client
runtime = runtime_fixture.runtime
loader = application_fixture.loader


class PublicationInterrupted(BaseException):
    """Model process cancellation immediately before current.json publication."""


class RuntimeStateRegression(unittest.TestCase):
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

    def test_completed_pinned_client_recovers_offline_after_interruption(self):
        """A complete pinned generation is recovered without network or installation."""
        fixture = self.fixture
        with (
            fixture.modules() as (root, installed),
            patch.dict(os.environ, {"DPE_CLIENT_PINNED": "1"}),
            patch.object(runtime, "download", side_effect=AssertionError("network forbidden")),
        ):
            original_write = client.write_json

            def interrupt_pointer(path, value):
                if path == root / "current.json":
                    raise PublicationInterrupted
                return original_write(path, value)

            with patch.object(client, "write_json", side_effect=interrupt_pointer):
                with self.assertRaises(PublicationInterrupted):
                    client.ensure_client(root, "canary")

            hosts = list((root / "generations").iterdir())
            self.assertEqual(len(hosts), 1)
            host = hosts[0]
            self.assertEqual(client.validate_host(root, "canary", host.name), host)
            state = client.read_object(host / "client.json")
            sources = client.approved_sources(root, "canary", offline=True, previous=None)
            self.assertEqual(state["approval"], "package")
            self.assertEqual(client.pins(state), client.pins(sources[0]))
            self.assertEqual(len(installed), len(client.REQUIRED))
            self.assertFalse((root / "current.json").exists())

            with patch.object(client, "install_module", side_effect=AssertionError("no install")):
                self.assertEqual(client.ensure_client(root, "canary", offline=True), host)
            self.assertEqual(client.current_host(root, "canary"), host)

            # Control: changing only offline=False recovers the exact generation
            # without installing modules or doing any network work.
            with patch.object(
                client, "install_module", side_effect=AssertionError("already cached")
            ) as install:
                self.assertEqual(client.ensure_client(root, "canary"), host)
                install.assert_not_called()
            self.assertEqual(client.ensure_client(root, "canary", offline=True), host)

    def test_nonexecutable_runtime_is_rejected_and_repaired_from_local_archive(self):
        fixture = self.fixture
        fixture.updater_only_package()
        with (
            patch.object(runtime, "probe"),
            patch.object(runtime, "download", side_effect=AssertionError("network forbidden")),
            patch.dict(os.environ, {"DPE_RUNTIME_UPDATES": "0", "DPE_CLIENT_OFFLINE": "1"}),
        ):
            executable = runtime.ensure_runtime(
                fixture.app, fixture.profile, offline=True, archive=fixture.archive
            )
            original_hash = runtime.checksum(executable)
            executable.chmod(0o600)
            with self.assertRaisesRegex(ValueError, "Incomplete or mismatched"):
                runtime.validate_generation(
                    fixture.cache, fixture.app, fixture.policy, executable.parent.name
                )
            with self.assertRaisesRegex(ValueError, "No verified Electron"):
                runtime.ensure_runtime(fixture.app, fixture.profile, offline=True)
            with patch.object(runtime_fixture.bootstrap.subprocess, "Popen") as helper:
                with self.assertRaisesRegex(ValueError, "No verified Electron"):
                    runtime_fixture.bootstrap.prepare(fixture.app, fixture.profile)
                helper.assert_not_called()
            self.assertEqual(
                runtime.ensure_runtime(
                    fixture.app, fixture.profile, offline=True, archive=fixture.archive
                ),
                executable,
            )
            self.assertTrue(os.access(executable, os.X_OK))
            self.assertEqual(runtime.checksum(executable), original_hash)
            self.assertEqual(
                runtime_fixture.bootstrap.prepare(fixture.app, fixture.profile), str(executable)
            )


class MultipleInstanceRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        application_fixture.ApplicationUpdates.setUpClass()

    @classmethod
    def tearDownClass(cls):
        application_fixture.ApplicationUpdates.tearDownClass()

    def setUp(self):
        self.fixture = application_fixture.ApplicationUpdates()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()

    def test_surviving_second_instance_defers_activation(self):
        """Exiting the first of two instances retains the survivor generation."""
        fixture = self.fixture
        alive = {11001: "100", 11002: "200", 11003: "300"}

        def launch(pid, *arguments):
            with (
                patch.object(loader, "__file__", str(fixture.package / "loader.py")),
                patch.object(loader.os, "getpid", return_value=pid),
                patch.object(loader, "process_identity", side_effect=alive.get),
                patch.object(
                    loader.sys,
                    "argv",
                    [
                        "loader.py",
                        "--profile",
                        str(fixture.profile),
                        "--launch",
                        "--",
                        *arguments,
                    ],
                ),
                patch.dict(os.environ, fixture.environment),
                patch.object(loader.os, "execve") as execute,
            ):
                loader.main()
                execute.assert_called_once()
                return execute.call_args.args

        first_path, _, environment = launch(11001, "--multi-instance")
        self.assertEqual(first_path, fixture.first_app / "launch.sh")
        # Simulate readiness without running the fixture launcher.
        Path(environment["DPE_APPLICATION_READY"]).write_text("ready")
        second_path, arguments, environment = launch(11002, "--multi-instance")
        self.assertEqual(second_path, first_path)
        self.assertIn("--multi-instance", arguments)
        self.assertNotIn("DPE_APPLICATION_READY", environment)

        candidate, archive = fixture.payload("second", "20261027.130000")
        proof = fixture.approve(candidate)
        new_generation = loader.install(
            fixture.root, fixture.package, fixture.policy, candidate, archive, proof
        )
        state = loader.read_state(fixture.root)
        state["pending"] = candidate["archive_sha256"]
        loader.write(fixture.root / "state.json", state)
        self.assertEqual(state["running"]["pid"], 11001)

        # Only the first instance exits; the independent second instance survives.
        del alive[11001]
        selected, _, environment = launch(11003, "--multi-instance")
        self.assertEqual(alive[11002], "200")
        self.assertEqual(selected, second_path)
        self.assertNotIn("DPE_APPLICATION_READY", environment)
        self.assertEqual(loader.read_state(fixture.root)["running"]["pid"], 11002)
        # Once every other instance exits, this process can hand off to pending code.
        del alive[11002]
        selected, _, environment = launch(11003, "--multi-instance")
        self.assertEqual(selected, new_generation / "app/launch.sh")
        self.assertIn("DPE_APPLICATION_READY", environment)


if __name__ == "__main__":
    unittest.main()
