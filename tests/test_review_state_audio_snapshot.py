"""Independent snapshot controls: synthetic caches, identities and disposable keys."""

import contextlib
import io
import os
import unittest
from unittest.mock import Mock, patch

import test_application_updates as applications
import test_runtime_updater as fixtures
import test_wayland_audio as audio_fixtures

client, runtime, loader = fixtures.client, fixtures.runtime, applications.loader


class SnapshotCacheReview(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.RuntimeUpdates.setUpClass()

    @classmethod
    def tearDownClass(cls):
        fixtures.RuntimeUpdates.tearDownClass()

    def setUp(self):
        self.fixture = fixtures.RuntimeUpdates()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()

    def test_missing_pointer_signed_cache_rechecks_approval_signature_and_application_cap(self):
        f = self.fixture
        f.manifest["application"] = {
            "bootstrap_api": 1,
            "asset": "application.zip",
            "version": f.manifest["version"],
            "archive_sha256": "d" * 64,
            "index_sha256": "e" * 64,
        }
        f.sign()
        with f.modules() as (root, installed):
            host = client.ensure_client(root, "canary")
            state = client.read_object(host / "client.json")
            self.assertNotEqual(state["approval"], "package")
            proof = root / "approvals" / state["approval"]
            preserved = {
                p: p.read_bytes()
                for directory in (host, root / "cache", proof)
                for p in directory.rglob("*")
                if p.is_file()
            }
            pointer = root / "current.json"
            pointer.unlink()
            count = len(installed)
            with (
                patch.object(runtime, "download", side_effect=AssertionError("no network")),
                patch.object(client, "download", side_effect=AssertionError("no network")),
                patch.object(client, "install_module", side_effect=AssertionError("no install")),
                patch.dict(os.environ, {"DPE_APPLICATION_VERSION": f.manifest["version"]}),
            ):
                self.assertEqual(client.ensure_client(root, "canary", offline=True), host)
                for name in ("approval.json", "SHA256SUMS.sig"):
                    with self.subTest(damaged=name):
                        pointer.unlink()
                        path = proof / name
                        before = path.read_bytes()
                        path.write_bytes(b"invalid")
                        with self.assertRaises(ValueError):
                            client.ensure_client(root, "canary", offline=True)
                        self.assertFalse(pointer.exists())
                        path.write_bytes(before)
                        self.assertEqual(client.ensure_client(root, "canary", offline=True), host)
                pointer.unlink()
                with patch.dict(os.environ, {"DPE_APPLICATION_VERSION": "20261024.120000"}):
                    with self.assertRaises(ValueError):
                        client.ensure_client(root, "canary", offline=True)
                self.assertFalse(pointer.exists())
                self.assertEqual(client.ensure_client(root, "canary", offline=True), host)
            self.assertEqual(len(installed), count)
            self.assertEqual({p: p.read_bytes() for p in preserved}, preserved)

    def test_unusable_pointer_recovers_package_without_touching_another_generation(self):
        f = self.fixture
        with f.modules() as (root, _installed):
            with patch.dict(os.environ, {"DPE_CLIENT_PINNED": "1"}):
                baseline = client.ensure_client(root, "canary")
            approved = client.ensure_client(root, "canary")
            self.assertNotEqual(baseline, approved)
            approved_bytes = (approved / "client.json").read_bytes()
            with (
                patch.dict(os.environ, {"DPE_CLIENT_PINNED": "1"}),
                patch.object(runtime, "download", side_effect=AssertionError("no network")),
                patch.object(client, "install_module", side_effect=AssertionError("no install")),
            ):
                for pointer in ('"' + "0" * 64 + '"', "null", "[]", "{"):
                    with self.subTest(pointer=pointer):
                        (root / "current.json").write_text(pointer)
                        self.assertEqual(
                            client.ensure_client(root, "canary", offline=True), baseline
                        )
                        self.assertEqual((approved / "client.json").read_bytes(), approved_bytes)

    def test_cross_application_runtime_reuse_rejects_permission_damage_and_preserves_source(self):
        f = self.fixture
        f.updater_only_package()
        with patch.object(runtime, "probe"):
            executable = runtime.ensure_runtime(f.app, f.profile, offline=True, archive=f.archive)
        other = f.root / "another app"
        (other / "resources").mkdir(parents=True)
        fixtures.write_json(other / "runtime-update.json", f.policy)
        before = executable.read_bytes()
        executable.chmod(0o600)
        with (
            patch.object(runtime, "download", side_effect=AssertionError("no network")),
            patch.object(runtime, "probe") as probe,
        ):
            with self.assertRaisesRegex(ValueError, "No verified Electron"):
                runtime.ensure_runtime(other, f.profile, offline=True)
            probe.assert_not_called()
            self.assertEqual(executable.stat().st_mode & 0o777, 0o600)
            executable.chmod(0o700)
            reused = runtime.ensure_runtime(other, f.profile, offline=True)
            self.assertNotEqual(reused, executable)
            self.assertEqual(reused.read_bytes(), before)
            self.assertEqual(executable.read_bytes(), before)
            self.assertEqual((executable.parent / "resources").resolve(), f.app / "resources")
            self.assertEqual((reused.parent / "resources").resolve(), other / "resources")


class SnapshotProcessReview(unittest.TestCase):
    def test_wayland_native_hint_survives_reparent_but_not_pid_reuse(self):
        audio = audio_fixtures.audio
        identities = {100: (100, "1000", 1), 200: (200, "2000", 500)}
        routes = []

        def route():
            result = Mock(capture_pid=900)
            routes.append(result)
            return result

        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(audio, "process_identity", side_effect=identities.get),
            patch.object(audio, "discord_root", return_value=identities[100]),
            patch.object(audio, "graph", return_value=[]),
            patch.object(audio, "command", return_value="[]"),
            patch.object(audio, "video_sources", return_value={(20, "300")}),
            patch.object(audio, "Route", side_effect=route),
            patch.object(audio.Controller, "choose", side_effect=AssertionError("no picker")),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            controller = audio.Controller(100)
            controller.update({"generation": 1, "enabled": True, "hint": 200})
            controller.tick()
            self.assertEqual(controller.last["state"], "ready")
            identities[200] = (200, "2000", 1)
            controller.tick()
            self.assertEqual(len(routes), 1)
            routes[0].close.assert_not_called()
            self.assertEqual(routes[0].sync.call_args.args[2], identities[200])
            identities[200] = (200, "2001", 1)
            for _ in range(3):
                controller.tick()
            routes[0].close.assert_called_once()
            self.assertIsNone(controller.route)
            self.assertEqual(controller.last["state"], "unavailable")
            controller.update({"generation": 2, "enabled": True, "hint": 200})
            controller.tick()
            self.assertEqual(len(routes), 2)
            self.assertEqual(controller.last["state"], "ready")
            controller.close()
            routes[1].close.assert_called_once()

    def test_proc_parser_and_instance_pruning_use_start_time_not_parent_or_command_name(self):
        identities = {10001: ["S", "100", 1], 10002: ["S", "200", 10001]}

        def read(path):
            pid = int(path.parts[2])
            if pid not in identities:
                raise FileNotFoundError()
            state, start, parent = identities[pid]
            fields = ["0"] * 20
            fields[0], fields[1], fields[19] = state, str(parent), start
            return f"{pid} (fixture (with) parentheses) " + " ".join(fields)

        state = {
            "running": {"pid": 10001, "process": "100", "digest": "a" * 64},
            "instances": [{"pid": 10002, "process": "200", "digest": "a" * 64}],
        }
        with patch.object(loader.Path, "read_text", read):
            self.assertEqual(len(loader.live_instances(state)), 2)
            identities[10002][2] = 1
            self.assertEqual(len(loader.live_instances(state)), 2)
            identities[10001][0] = "Z"
            self.assertEqual(loader.live_instances(state), state["instances"])
            identities[10002][1] = "201"
            self.assertEqual(loader.live_instances(state), [])
            identities[10001][0] = "X"
            self.assertEqual(loader.live_instances(state), [])
            identities.clear()
            self.assertEqual(loader.live_instances(state), [])
