"""Regression: real loader rollback recovers a complete pinned client offline.

Synthetic indexed applications and modules, disposable signing key, mocked exec.
The execution boundary stays mocked while selection and authorization remain real.
"""

import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import test_application_updates as applications
import test_runtime_updater as runtimes

client = runtimes.client
loader = applications.loader


class VerifiedRollbackRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        runtimes.RuntimeUpdates.setUpClass()

    @classmethod
    def tearDownClass(cls):
        runtimes.RuntimeUpdates.tearDownClass()

    def setUp(self):
        self.fixture = runtimes.RuntimeUpdates()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()

    def payload(self, label, policy, pins):
        fixture = self.fixture
        source = fixture.root / label
        source.mkdir()
        (source / "launch.sh").write_text("#!/bin/sh\nexit 99\n")
        (source / "launch.sh").chmod(0o755)
        (source / "discord.png").write_bytes(b"synthetic icon")
        runtimes.write_json(source / "client-update.json", pins)
        runtimes.write_json(source / "runtime-update.json", policy)
        key = source / "integration/updater/release-signing-key.asc"
        key.parent.mkdir(parents=True)
        key.write_bytes(fixture.public)
        archive = fixture.root / (label + ".zip")
        descriptor = applications.bundle(source, archive, policy["release_version"], 1793102400)
        return source, descriptor, archive

    def test_real_trial_failure_rolls_back_and_recovers_offline_client(self):
        fixture = self.fixture
        source_a, candidate_a, archive_a = self.payload(
            "A", fixture.policy, client.read_object(fixture.app / "client-update.json")
        )
        _, candidate_b, archive_b = self.payload(
            "B",
            {
                **fixture.policy,
                "release_version": fixture.manifest["version"],
                "electron_version": fixture.manifest["electron_version"],
                "runtime_key": fixture.manifest["runtime"]["runtime_key"],
                "archive_sha256": fixture.manifest["runtime"]["archive_sha256"],
            },
            fixture.manifest["client"],
        )
        package = fixture.root / "bootstrap package"
        applications.bootstrap(source_a, package, candidate_a, "fixture", "canary", "prerelease")
        (package / "release-signing-key.asc").write_bytes(fixture.public)
        fixture.manifest.update(
            application=candidate_b, assets={"application.zip": candidate_b["archive_sha256"]}
        )
        fixture.sign()
        sums = fixture.release / "SHA256SUMS"
        sums.write_text(sums.read_text() + candidate_b["archive_sha256"] + "  application.zip\n")
        subprocess.run(
            [
                *fixture.signer,
                "--yes",
                "--detach-sign",
                "--output",
                str(fixture.release / "SHA256SUMS.sig"),
                str(sums),
            ],
            check=True,
            capture_output=True,
        )
        runtimes.write_json(fixture.release / "github.json", fixture.github)
        runtimes.write_json(fixture.release / "approval.json", fixture.approval)
        application_root = loader.cache_root(package, fixture.profile)
        alive = {31001: "100", 31002: "200", 31003: "300"}

        def invoke(pid, *arguments, offline=False):
            with (
                patch.object(loader, "__file__", str(package / "loader.py")),
                patch.object(loader.os, "getpid", return_value=pid),
                patch.object(loader, "process_identity", side_effect=alive.get),
                patch.object(
                    loader.sys,
                    "argv",
                    ["loader.py", "--profile", str(fixture.profile), *arguments],
                ),
                patch.dict(os.environ, {"DPE_CLIENT_OFFLINE": "1" if offline else "0"}),
                patch.object(loader.os, "execve") as execute,
                patch.object(loader, "download", side_effect=AssertionError("network forbidden")),
            ):
                loader.main()
                if "--launch" in arguments:
                    execute.assert_called_once()
                    path, _, environment = execute.call_args.args
                    self.assertEqual(path, Path(environment["DPE_APPLICATION"]) / "launch.sh")
                    return Path(environment["DPE_APPLICATION"]), environment
                execute.assert_not_called()
                return None

        with fixture.modules() as (root, installed):
            app_a, environment_a = invoke(31001, "--launch", "--archive", str(archive_a))
            with patch.dict(os.environ, environment_a), patch.object(client, "PACKAGE_ROOT", app_a):
                baseline = client.ensure_client(root, "canary")
            Path(environment_a["DPE_APPLICATION_READY"]).write_text("ready\n")
            del alive[31001]
            # The real stage path consumes readiness, verifies B, and records pending B.
            invoke(31002, "--stage", str(fixture.release), "--archive", str(archive_b))
            self.assertEqual(
                loader.read_state(application_root)["current"], candidate_a["archive_sha256"]
            )
            app_b, environment_b = invoke(31002, "--launch")
            self.assertEqual(environment_b["DPE_APPLICATION_VERSION"], candidate_b["version"])
            (root / "approvals/last-check.json").unlink()
            with patch.dict(os.environ, environment_b), patch.object(client, "PACKAGE_ROOT", app_b):
                updated = client.ensure_client(root, "canary")
            self.assertEqual(client.read_object(updated / "client.json")["version"], "1.0.124")
            self.assertNotEqual(updated, baseline)
            self.assertFalse(Path(environment_b["DPE_APPLICATION_READY"]).exists())
            # B exits after client preparation and before the Discord HTTPS window is ready.
            del alive[31002]
            restored, environment = invoke(31003, "--launch", offline=True)
            self.assertEqual(restored, app_a)
            self.assertEqual(environment["DPE_APPLICATION_VERSION"], candidate_a["version"])
            self.assertNotIn("DPE_APPLICATION_READY", environment)
            self.assertEqual(client.current_host(root, "canary"), updated)
            count = len(installed)
            with (
                patch.dict(os.environ, environment),
                patch.object(client, "PACKAGE_ROOT", restored),
                patch.object(
                    runtimes.runtime, "download", side_effect=AssertionError("network forbidden")
                ) as network,
                patch.object(
                    client, "download", side_effect=AssertionError("module network forbidden")
                ) as module_network,
                patch.object(
                    client, "install_module", side_effect=AssertionError("install forbidden")
                ) as install,
            ):
                self.assertEqual(client.validate_host(root, "canary", baseline.name), baseline)
                sources = client.approved_sources(root, "canary", offline=True, previous=updated)
                self.assertEqual(len(sources), 1)
                self.assertEqual(
                    client.pins(sources[0]),
                    client.pins(client.read_object(baseline / "client.json")),
                )
                self.assertEqual(client.ensure_client(root, "canary", offline=True), baseline)
                self.assertEqual(client.current_host(root, "canary"), baseline)
                with patch.dict(os.environ, {"DPE_CLIENT_OFFLINE": "0"}):
                    self.assertEqual(client.ensure_client(root, "canary"), baseline)
                self.assertEqual(client.ensure_client(root, "canary", offline=True), baseline)
                network.assert_not_called()
                module_network.assert_not_called()
                install.assert_not_called()
            self.assertEqual(len(installed), count)
            print(
                "Offline and normal mode recovered the identical A generation; zero downloads/installations."
            )
