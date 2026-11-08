"""Maintenance trust tests use synthetic payloads and an ephemeral signing key."""

import hashlib
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "dpe_kms_maintenance_test", REPO / "integration/video/kms-maintenance.py"
)
maintenance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(maintenance)


class Maintenance(unittest.TestCase):
    def test_root_files_reject_user_owned_links_and_oversize_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            target = base / "data"
            target.write_bytes(b"data")
            self.assertEqual(maintenance.read_file(target, 4), b"data")
            with self.assertRaises(ValueError):
                maintenance.read_file(target, 3)
            if os.getuid() != 0:
                with self.assertRaises(ValueError):
                    maintenance.read_file(target, 4, root=True)
            (base / "link").symlink_to(target)
            with self.assertRaises(OSError):
                maintenance.read_file(base / "link", 4)

    def test_capability_requires_the_kernel_peer_pid_and_fixed_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            endpoint = str(Path(temporary) / "socket")
            token = "a" * 64
            app = Path(temporary) / "application"
            for wrong_pid, wrong_worker in ((False, False), (True, False), (False, True)):
                server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                server.bind(endpoint)
                server.listen(1)

                def serve(server=server, wrong_worker=wrong_worker):
                    with server:
                        peer, _ = server.accept()
                        with peer:
                            peer.settimeout(2)
                            request = peer.recv(65)
                            if request:
                                peer.sendall(
                                    b"/wrong\n"
                                    if wrong_worker
                                    else str(maintenance.ROOT / "kms-maintenance").encode()
                                    + b"\n"
                                    + os.fsencode(app)
                                    + b"\n"
                                )

                thread = threading.Thread(target=serve)
                thread.start()
                try:
                    if wrong_pid or wrong_worker:
                        with self.assertRaises(ValueError):
                            maintenance.authorize(
                                os.getuid(), os.getpid() + int(wrong_pid), endpoint, token, app
                            )
                    else:
                        maintenance.authorize(
                            os.getuid(), os.getpid(), endpoint, token, app
                        ).close()
                finally:
                    thread.join(timeout=3)
                    self.assertFalse(thread.is_alive())
                    Path(endpoint).unlink()

    def test_full_refresh_uses_authenticated_application_without_procfs_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = root / "app"
            for role, (relative, _) in maintenance.ROLES.items():
                path = app / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"\x7fELF fixture" if role == "server" else b"fixture source")
            spec = importlib.util.spec_from_file_location(
                "kms_binding_fixture", REPO / "integration/video/kms-permission-setup.py"
            )
            setup = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(setup)
            _, runtime = maintenance.runtime_identity(setup, os.getuid(), os.getpid())
            incoming, _ = maintenance.descriptor(app, runtime)
            (root / "maintenance").mkdir()
            (root / "maintenance" / str(os.getuid())).write_text(
                json.dumps({"schema": 1, "enabled": True, "current": incoming})
            )
            (root / "maintenance-authority.json").write_text(
                json.dumps({"schema": 1, "components": {r: incoming[r] for r in maintenance.ROLES}})
            )
            endpoint = str(root / "socket")
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(endpoint)
            listener.listen(1)
            stop = threading.Event()
            token = "b" * 64

            def serve():
                with listener:
                    peer, _ = listener.accept()
                    with peer:
                        request = b""
                        while not request.endswith(b"\n"):
                            request += peer.recv(65)
                        self.assertEqual(request, (token + "\n").encode())
                        peer.sendall(
                            os.fsencode(root / "kms-maintenance") + b"\n" + os.fsencode(app) + b"\n"
                        )
                        stop.wait(3)

            thread = threading.Thread(target=serve)
            thread.start()
            original = maintenance.read_file
            try:
                with (
                    patch.object(maintenance, "ROOT", root),
                    patch.object(
                        maintenance,
                        "read_file",
                        side_effect=lambda path, limit, **_: original(path, limit),
                    ),
                    patch.object(setup, "apply") as apply,
                ):
                    maintenance.refresh(setup, os.getuid(), os.getpid(), app, endpoint, token)
                    apply.assert_called_once()
            finally:
                stop.set()
                thread.join(timeout=4)
                self.assertFalse(thread.is_alive())

    def test_refresh_authorizes_only_current_or_newly_approved_exact_components(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = root / "app"
            for _, (relative, _) in maintenance.ROLES.items():
                path = app / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(
                    b"\x7fELF fixture" if path.name == "dpe-kms-server" else b"fixture source"
                )
            incoming, _ = maintenance.descriptor(app, "a" * 64)
            current = {**incoming, "server": "b" * 64}
            state = {
                "schema": 1,
                "enabled": True,
                "channel": "canary",
                "track": "prerelease",
                "current": incoming,
                "release_version": "20261101.000001",
            }
            global_state = {
                "schema": 1,
                "components": {r: incoming[r] for r in maintenance.ROLES},
                "release_version": "20261101.000001",
            }
            (root / "maintenance").mkdir()
            record = root / "maintenance" / str(os.getuid())
            record.write_text(json.dumps(state))
            (root / "maintenance-authority.json").write_text(json.dumps(global_state))
            writes = []
            setup = SimpleNamespace(
                as_user=lambda _: nullcontext(),
                executable_grant=lambda *_: b"binding",
                apply=lambda *args, **kwargs: writes.append((args, kwargs)),
            )
            original = maintenance.read_file

            def reader(path, limit, **_):
                return original(path, limit)

            caller, owner = socket.socketpair()
            with (
                patch.object(maintenance, "ROOT", root),
                patch.object(maintenance, "authorize", side_effect=lambda *_: caller.dup()),
                patch.object(maintenance, "runtime_identity", return_value=(b"binding", "a" * 64)),
                patch.object(maintenance, "read_file", side_effect=reader),
                patch.object(maintenance, "approved_release") as approved,
            ):
                maintenance.refresh(setup, os.getuid(), os.getpid(), app, "unused", "a" * 64)
                self.assertEqual(len(writes), 1)
                approved.assert_not_called()
                # An edited helper cannot turn itself into a trusted release.
                state["current"] = current
                record.write_text(json.dumps(state))
                release = {
                    "version": "20261102.000001",
                    "capture_maintenance": {"schema": 1, "components": incoming},
                }
                approved.return_value = release
                maintenance.refresh(setup, os.getuid(), os.getpid(), app, "unused", "a" * 64)
                self.assertEqual(
                    writes[-1][1]["maintenance"][2]["release_version"], release["version"]
                )
                approved.return_value = {
                    **release,
                    "capture_maintenance": {"schema": 1, "components": current},
                }
                with self.assertRaises(ValueError):
                    maintenance.refresh(setup, os.getuid(), os.getpid(), app, "unused", "a" * 64)
                approved.return_value = {**release, "version": "20261031.000001"}
                with self.assertRaises(ValueError):
                    maintenance.refresh(setup, os.getuid(), os.getpid(), app, "unused", "a" * 64)
                # Another account/channel cannot silently replace newer shared
                # privileged code with its older, individually cached tuple.
                state["current"] = incoming
                record.write_text(json.dumps(state))
                global_state["components"]["server"] = "c" * 64
                global_state["release_version"] = "20261109.000001"
                (root / "maintenance-authority.json").write_text(json.dumps(global_state))
                approved.return_value = release
                before = len(writes)
                with self.assertRaises(ValueError):
                    maintenance.refresh(setup, os.getuid(), os.getpid(), app, "unused", "a" * 64)
                self.assertEqual(len(writes), before)
                state["enabled"] = False
                record.write_text(json.dumps(state))
                with self.assertRaises(ValueError):
                    maintenance.refresh(setup, os.getuid(), os.getpid(), app, "unused", "a" * 64)
            caller.close()
            owner.close()


@unittest.skipUnless(shutil.which("gpg") and shutil.which("gpgv"), "GnuPG required")
class SignedMaintenance(unittest.TestCase):
    def test_real_signature_and_independent_approval_reject_forgery_and_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "gpg"
            home.mkdir(mode=0o700)
            command = [
                "gpg",
                "--batch",
                "--homedir",
                str(home),
                "--pinentry-mode",
                "loopback",
                "--passphrase",
                "",
            ]
            subprocess.run(
                [
                    *command,
                    "--quick-gen-key",
                    "DPE maintenance fixture <fixture@example.invalid>",
                    "ed25519",
                    "sign",
                    "0",
                ],
                check=True,
                capture_output=True,
                timeout=20,
            )
            key = subprocess.check_output([*command, "--armor", "--export"], timeout=10)
            (root / "release-signing-key.asc").write_bytes(key)
            release = {
                "schema": 1,
                "signing_required": True,
                "source_dirty": False,
                "channel": "canary",
                "electron_version": "46.0.0-alpha.1",
                "version": "20261107.000001",
                "capture_maintenance": {"schema": 1, "components": {}},
            }
            manifest = json.dumps(release).encode()
            digest = hashlib.sha256(manifest).hexdigest()
            sums = (digest + "  release.json\n").encode()
            (root / "SHA256SUMS").write_bytes(sums)
            subprocess.run(
                [*command, "--detach-sign", str(root / "SHA256SUMS")],
                check=True,
                capture_output=True,
                timeout=10,
            )
            approval = {
                "schema": 1,
                "tag": "v20261107000001.0.0-canary.fixture",
                "version": release["version"],
                "manifest_sha256": digest,
                "approved_by": "fixture",
            }
            files = {
                "release.json": manifest,
                "SHA256SUMS": sums,
                "SHA256SUMS.sig": (root / "SHA256SUMS.sig").read_bytes(),
            }

            def fetch(url, limit):
                data = (
                    json.dumps(approval).encode()
                    if url.endswith("approved-release.json")
                    else files[url.rsplit("/", 1)[-1]]
                )
                self.assertLessEqual(len(data), limit)
                return data

            original = maintenance.read_file
            with (
                patch.object(maintenance, "ROOT", root),
                patch.object(
                    maintenance,
                    "read_file",
                    side_effect=lambda path, limit, **_: original(path, limit),
                ),
                patch.object(maintenance, "fetch", side_effect=fetch),
            ):
                policy = {"channel": "canary", "track": "prerelease"}
                self.assertEqual(maintenance.approved_release(policy), release)
                files["release.json"] = manifest + b" "
                with self.assertRaises(ValueError):
                    maintenance.approved_release(policy)
                files["release.json"] = manifest
                approval["manifest_sha256"] = "f" * 64
                with self.assertRaises(ValueError):
                    maintenance.approved_release(policy)
                approval["manifest_sha256"] = digest
                files["SHA256SUMS"] = sums + b"forged\n"
                with self.assertRaises(subprocess.CalledProcessError):
                    maintenance.approved_release(policy)
            subprocess.run(
                ["gpgconf", "--homedir", str(home), "--kill", "gpg-agent"],
                check=True,
                capture_output=True,
                timeout=5,
            )


if __name__ == "__main__":
    unittest.main()
