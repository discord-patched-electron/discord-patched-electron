"""Signed application generations update all executable code without package writes."""

import copy
import errno
import importlib.util
import io
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "integration/updater"))
import application
import runtime
from application_payload import bootstrap, bundle, validate_bootstrap
from common import read_json, sha256, write_json

spec = importlib.util.spec_from_file_location(
    "application_loader", ROOT / "integration/bootstrap/loader.py"
)
loader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loader)


@unittest.skipUnless(shutil.which("gpg") and shutil.which("gpgv"), "GnuPG required")
class ApplicationUpdates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys = tempfile.TemporaryDirectory(prefix="application-signing-fixture-")
        cls.signer = ["gpg", "--batch", "--homedir", cls.keys.name]
        subprocess.run(
            [
                *cls.signer,
                "--pinentry-mode",
                "loopback",
                "--passphrase",
                "",
                "--quick-generate-key",
                "Disposable application test",
                "ed25519",
                "sign",
                "1d",
            ],
            check=True,
            capture_output=True,
        )
        cls.public = subprocess.check_output([*cls.signer, "--armor", "--export"])

    @classmethod
    def tearDownClass(cls):
        subprocess.run(["gpgconf", "--homedir", cls.keys.name, "--kill", "gpg-agent"], check=True)
        cls.keys.cleanup()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.package = self.directory / "installed package"
        self.profile = self.directory / "fresh profile"
        self.root = loader.cache_root(self.package, self.profile)
        self.first, self.archive = self.payload("first", "20261027.120000")
        bootstrap(
            self.directory / "first",
            self.package,
            self.first,
            "fixture-tag",
            "canary",
            "prerelease",
        )
        (self.package / "release-signing-key.asc").write_bytes(self.public)
        self.policy = read_json(self.package / "bootstrap.json")
        self.command = [
            sys.executable,
            "-I",
            str(self.package / "loader.py"),
            "--profile",
            str(self.profile),
        ]
        self.environment = {
            **os.environ,
            "DPE_CLIENT_OFFLINE": "1",
            "DISPLAY": "",
            "WAYLAND_DISPLAY": "",
        }
        self.first_app = Path(
            self.run_loader("--resolve", "--archive", str(self.archive)).stdout.strip()
        )
        self.package_files = {
            str(path.relative_to(self.package)): sha256(path)
            for path in self.package.rglob("*")
            if path.is_file()
        }

    def payload(self, label, version, *, broken=False):
        source = self.directory / label
        source.mkdir()
        script = (
            '#!/bin/sh\nif [ "${1:-}" = cancel ]; then exit 1; fi\n'
            'if [ -n "${DPE_APPLICATION_STARTED:-}" ]; then printf started > "$DPE_APPLICATION_STARTED"; fi\n'
            'if [ "${1:-}" = crash ]; then exit 1; fi\n'
        )
        script += (
            "exit 1\n"
            if broken
            else 'if [ -n "${DPE_APPLICATION_READY:-}" ]; then printf ready > "$DPE_APPLICATION_READY"; fi\n'
            + f'printf "%s\\n" {label}\n'
        )
        (source / "launch.sh").write_text(script)
        (source / "launch.sh").chmod(0o755)
        (source / "updater.py").write_text(f'VERSION = "{label}"\n')
        (source / "patches.cjs").write_text(f'module.exports = "{label}";\n')
        (source / "app.asar").write_bytes(label.encode())
        (source / "discord.png").write_bytes(b"fixture")
        (source / "discord-canary.desktop").write_text("[Desktop Entry]\n")
        archive = self.directory / (label + ".zip")
        return bundle(source, archive, version, 1793102400), archive

    def run_loader(self, *args, check=True):
        return subprocess.run(
            [*self.command, *args],
            env=self.environment,
            text=True,
            capture_output=True,
            check=check,
        )

    def approve(self, candidate, *, approved=True):
        proof = self.directory / (candidate["version"] + "-proof")
        proof.mkdir(exist_ok=True)
        manifest = {
            "application": candidate,
            "version": candidate["version"],
            "channel": "canary",
            "signing_required": True,
            "source_dirty": False,
            "electron_version": "46.0.0-alpha.1",
            "minimum_glibc": "2.1",
            "assets": {"application.zip": candidate["archive_sha256"]},
            "runtime": {
                "version": "46.0.0-alpha.1",
                "runtime_key": "a" * 64,
                "archive_sha256": "b" * 64,
                "cache_release": "runtime-cache-" + "a" * 64,
            },
        }
        write_json(proof / "release.json", manifest)
        digest = sha256(proof / "release.json")
        (proof / "SHA256SUMS").write_text(
            digest + "  release.json\n" + candidate["archive_sha256"] + "  application.zip\n"
        )
        subprocess.run(
            [
                *self.signer,
                "--yes",
                "--detach-sign",
                "--output",
                str(proof / "SHA256SUMS.sig"),
                str(proof / "SHA256SUMS"),
            ],
            check=True,
            capture_output=True,
        )
        tag = "v" + candidate["version"]
        write_json(
            proof / "github.json",
            {
                "tag_name": tag,
                "draft": False,
                "prerelease": not approved,
                "assets": [
                    {
                        "name": name,
                        "browser_download_url": f"https://github.com/{runtime.REPOSITORY}/releases/download/{tag}/{name}",
                    }
                    for name in ("release.json", "SHA256SUMS", "SHA256SUMS.sig")
                ],
            },
        )
        write_json(
            proof / "approval.json",
            {
                "schema": 1,
                "tag": tag,
                "version": candidate["version"],
                "manifest_sha256": digest,
                "approved_by": "test-maintainer" if approved else "",
            },
        )
        return proof

    def stage(self, candidate, archive, **kwargs):
        return self.run_loader(
            "--stage", str(self.approve(candidate)), "--archive", str(archive), **kwargs
        )

    def test_fresh_install_and_offline_relaunch_do_not_write_to_package(self):
        self.assertEqual(self.run_loader("--launch").stdout.strip(), "first")
        self.assertEqual(self.run_loader("--launch").stdout.strip(), "first")
        self.assertEqual(
            read_json(self.root / "state.json")["current"], self.first["archive_sha256"]
        )
        self.assertEqual(
            self.package_files,
            {
                str(path.relative_to(self.package)): sha256(path)
                for path in self.package.rglob("*")
                if path.is_file()
            },
        )
        validate_bootstrap(self.package)
        self.assertFalse((self.package / "updater.py").exists())
        self.assertFalse((self.package / "app.asar").exists())

    def test_cancelled_first_launch_retries_the_verified_cache_offline(self):
        self.assertEqual(self.run_loader("--launch", "--", "cancel", check=False).returncode, 1)
        result = self.run_loader("--launch", check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "first")
        self.run_loader("--resolve")
        state = read_json(self.root / "state.json")
        self.assertEqual(state["current"], self.first["archive_sha256"])
        self.assertNotIn(self.first["archive_sha256"], state.get("rejected", []))

    def test_damaged_activation_state_recovers_the_pinned_cache_offline(self):
        for contents in (
            '{"trial":',
            "null",
            "[]",
            '{"current": []}',
            '{"rejected": null}',
            '{"running": {"pid": 1}}',
            '{"trial": {"token": "../../escape"}}',
        ):
            with self.subTest(contents=contents):
                (self.root / "state.json").write_text(contents)
                result = self.run_loader("--launch", check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "first")
        self.assertEqual(len(list(self.root.glob("state.damaged-*.json"))), 7)

    def test_malformed_generation_metadata_can_be_repaired_from_verified_archive(self):
        (self.first_app.parent / "application.json").write_text("null")
        result = self.run_loader("--resolve", "--archive", str(self.archive), check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.run_loader("--launch").stdout.strip(), "first")

    def test_missing_launcher_permission_is_repaired_before_execution(self):
        (self.first_app / "launch.sh").chmod(0o600)
        with self.assertRaisesRegex(ValueError, "executable"):
            loader.validate(self.first_app.parent, self.first)
        self.run_loader("--resolve", "--archive", str(self.archive))
        self.assertEqual(self.run_loader("--launch").stdout.strip(), "first")

    def test_interrupted_extraction_leaves_the_working_generation_untouched(self):
        self.run_loader("--launch")
        second, archive = self.payload("second", "20261027.130000")
        proof = self.approve(second)
        state = (self.root / "state.json").read_bytes()
        with patch.object(
            loader.shutil, "copyfileobj", side_effect=OSError(errno.ENOSPC, "No space left")
        ):
            with self.assertRaises(OSError):
                loader.install(self.root, self.package, self.policy, second, archive, proof)
        self.assertFalse((self.root / "generations" / second["archive_sha256"]).exists())
        self.assertFalse(list(self.root.glob("prepare-*")))
        self.assertEqual((self.root / "state.json").read_bytes(), state)
        self.assertEqual(self.run_loader("--launch").stdout.strip(), "first")

    def test_simultaneous_fresh_resolves_publish_one_complete_generation(self):
        shutil.rmtree(self.root)
        command = [*self.command, "--resolve", "--archive", str(self.archive)]
        with (
            subprocess.Popen(
                command, env=self.environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            ) as first,
            subprocess.Popen(
                command, env=self.environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            ) as second,
        ):
            for task in (first, second):
                stdout, stderr = task.communicate(timeout=10)
                self.assertEqual(task.returncode, 0, stderr.decode())
                self.assertEqual(Path(stdout.decode().strip()), self.first_app)
        self.assertEqual(len(list((self.root / "generations").iterdir())), 1)
        self.assertFalse(list(self.root.glob("prepare-*")))
        self.assertEqual(self.run_loader("--launch").stdout.strip(), "first")

    def test_exited_unreaped_trial_does_not_steal_the_next_launch_confirmation(self):
        with subprocess.Popen(
            [*self.command, "--launch", "--", "cancel"],
            env=self.environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ) as task:
            # Observe exit without reaping it: /proc still exposes its start time.
            os.waitid(os.P_PID, task.pid, os.WEXITED | os.WNOWAIT)
            result = self.run_loader("--launch", check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "first")
            state = read_json(self.root / "state.json")
            self.assertNotEqual(state["trial"]["pid"], task.pid)
            self.assertTrue((self.root / ("ready-" + state["trial"]["token"])).is_file())
        self.run_loader("--resolve")
        self.assertEqual(
            read_json(self.root / "state.json")["current"], self.first["archive_sha256"]
        )

    def test_signed_same_runtime_application_upgrade_changes_python_asar_and_patches(self):
        self.run_loader("--launch")
        second, archive = self.payload("second", "20261027.130000")
        new_app = Path(self.stage(second, archive).stdout.strip())
        self.assertNotEqual(new_app, self.first_app)
        self.assertEqual((self.first_app / "app.asar").read_bytes(), b"first")
        self.assertIn("second", (new_app / "updater.py").read_text())
        self.assertIn("second", (new_app / "patches.cjs").read_text())
        self.assertEqual(self.run_loader("--launch").stdout.strip(), "second")
        self.run_loader("--resolve")
        self.assertEqual(read_json(self.root / "state.json")["current"], second["archive_sha256"])

    def test_failed_startup_rolls_back_and_does_not_retry_rejected_payload(self):
        self.run_loader("--launch")
        second, archive = self.payload("broken", "20261027.130000", broken=True)
        self.stage(second, archive)
        self.assertEqual(self.run_loader("--launch", check=False).returncode, 1)
        self.assertEqual(self.run_loader("--launch").stdout.strip(), "first")
        state = read_json(self.root / "state.json")
        self.assertIn(second["archive_sha256"], state["rejected"])
        self.assertEqual(Path(self.stage(second, archive).stdout.strip()), self.first_app)

    def test_interrupted_update_setup_remains_pending_and_can_finish_offline(self):
        self.run_loader("--launch")
        second, archive = self.payload("second", "20261027.130000")
        self.stage(second, archive)
        self.run_loader("--launch")
        self.run_loader("--resolve")
        third, archive = self.payload("third", "20261028.120000")
        third_app = Path(self.stage(third, archive).stdout.strip())
        for _ in range(2):
            self.assertEqual(self.run_loader("--launch", "--", "cancel", check=False).returncode, 1)
            self.assertEqual(Path(self.run_loader("--resolve").stdout.strip()), third_app)
            state = read_json(self.root / "state.json")
            self.assertEqual(state["current"], second["archive_sha256"])
            self.assertEqual(state["pending"], third["archive_sha256"])
            self.assertNotIn(third["archive_sha256"], state.get("rejected", []))
            self.assertEqual(Path(self.stage(third, archive).stdout.strip()), third_app)
        self.assertEqual(self.run_loader("--launch").stdout.strip(), "third")
        self.run_loader("--resolve")
        self.assertEqual(read_json(self.root / "state.json")["current"], third["archive_sha256"])
        with patch.dict(os.environ, {"DPE_APPLICATION_VERSION": third["version"]}):
            self.assertEqual(
                runtime.approved_release(
                    self.approve(third), self.policy, self.package / "release-signing-key.asc"
                )["application"],
                third,
            )

    def test_changed_package_pin_retries_a_previously_rejected_update(self):
        self.run_loader("--launch")
        second, archive = self.payload("second", "20261027.130000")
        self.stage(second, archive)
        self.run_loader("--launch")
        third, archive = self.payload("third", "20261028.120000")
        self.stage(third, archive)
        self.assertEqual(self.run_loader("--launch", "--", "crash", check=False).returncode, 1)
        self.assertEqual(self.run_loader("--launch").stdout.strip(), "second")
        self.assertIn(third["archive_sha256"], read_json(self.root / "state.json")["rejected"])
        write_json(self.package / "bootstrap.json", {**self.policy, "application": third})
        self.assertEqual(self.run_loader("--launch").stdout.strip(), "third")
        self.run_loader("--resolve")
        state = read_json(self.root / "state.json")
        self.assertEqual(state["current"], third["archive_sha256"])
        self.assertNotIn(third["archive_sha256"], state["rejected"])

    def test_confirmed_launch_does_not_inherit_trial_markers(self):
        self.run_loader("--launch")
        self.run_loader("--resolve")
        started, ready = self.directory / "stale-started", self.directory / "stale-ready"
        self.environment.update(
            DPE_APPLICATION_STARTED=str(started), DPE_APPLICATION_READY=str(ready)
        )
        self.assertEqual(self.run_loader("--launch").stdout.strip(), "first")
        self.assertFalse(started.exists())
        self.assertFalse(ready.exists())

    def test_tampered_cache_falls_back_without_changing_older_files(self):
        self.run_loader("--launch")
        second, archive = self.payload("second", "20261027.130000")
        new_app = Path(self.stage(second, archive).stdout.strip())
        (new_app / "updater.py").write_text("untrusted edit")
        self.assertEqual(self.run_loader("--launch").stdout.strip(), "first")
        self.assertEqual((self.first_app / "app.asar").read_bytes(), b"first")

    def test_unapproved_or_tampered_release_cannot_supply_executable_code(self):
        second, archive = self.payload("second", "20261027.130000")
        for field, value in (
            ("approved_by", ""),
            ("manifest_sha256", "c" * 64),
            ("tag", "different"),
        ):
            proof = self.approve(second)
            record = read_json(proof / "approval.json")
            write_json(proof / "approval.json", {**record, field: value})
            with self.subTest(field=field):
                result = self.run_loader(
                    "--stage", str(proof), "--archive", str(archive), check=False
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / "generations" / second["archive_sha256"]).exists())
        proof = self.approve(second)
        (proof / "release.json").write_text("{}")
        self.assertNotEqual(
            self.run_loader(
                "--stage", str(proof), "--archive", str(archive), check=False
            ).returncode,
            0,
        )

    def test_wrong_channel_newer_bootstrap_and_corrupt_archive_fail_closed(self):
        second, archive = self.payload("second", "20261027.130000")
        incompatible = {**second, "bootstrap_api": 2}
        self.assertNotEqual(self.stage(incompatible, archive, check=False).returncode, 0)
        proof = self.approve(second)
        policy = {**self.policy, "channel": "ptb"}
        write_json(self.package / "bootstrap.json", policy)
        self.assertNotEqual(
            self.run_loader(
                "--stage", str(proof), "--archive", str(archive), check=False
            ).returncode,
            0,
        )
        write_json(self.package / "bootstrap.json", self.policy)
        archive.write_bytes(b"truncated download")
        self.assertNotEqual(self.stage(second, archive, check=False).returncode, 0)

    def test_running_generation_is_not_replaced_and_restart_is_advertised(self):
        self.run_loader("--launch")
        second, archive = self.payload("second", "20261027.130000")
        proof = self.approve(second)

        def download(url, target, **_kwargs):
            name = (
                "github.json"
                if url.endswith("/latest")
                else "approval.json"
                if "/config/" in url
                else url.rsplit("/", 1)[-1]
            )
            shutil.copyfile(archive if name == "application.zip" else proof / name, target)

        with (
            patch.dict(
                os.environ,
                {
                    "DPE_INSTALL_ROOT": str(self.package),
                    "DPE_CLIENT_OFFLINE": "0",
                    "DPE_CLIENT_PINNED": "0",
                },
            ),
            patch.object(runtime, "download", side_effect=download),
            patch.object(application, "download", side_effect=download),
        ):
            incoming = application.prepare(self.first_app, self.profile)
            self.assertIsNotNone(incoming)
            self.assertEqual((self.first_app / "updater.py").read_text(), 'VERSION = "first"\n')
            self.assertEqual(application.prepare(self.first_app, self.profile), incoming)
        self.assertEqual(self.run_loader("--launch").stdout.strip(), "second")

    def test_a_second_launch_preserves_the_running_application_until_restart(self):
        self.run_loader("--launch")
        self.run_loader("--resolve")
        state = read_json(self.root / "state.json")
        state["running"] = {
            "pid": os.getpid(),
            "process": loader.process_identity(os.getpid()),
            "digest": self.first["archive_sha256"],
        }
        write_json(self.root / "state.json", state)
        second, archive = self.payload("second", "20261027.130000")
        self.stage(second, archive)
        with patch.object(loader.os, "getpid", return_value=99999999):
            selected, token = loader.select(self.root, self.package, self.policy, launch=True)
        self.assertEqual(selected, self.first_app)
        self.assertIsNone(token)
        self.assertEqual(read_json(self.root / "state.json")["pending"], second["archive_sha256"])

    def test_startup_handoff_can_select_pending_code_in_the_same_process(self):
        second, archive = self.payload("second", "20261027.130000")
        self.stage(second, archive)
        state = read_json(self.root / "state.json")
        state["trial"] = {
            "pid": os.getpid(),
            "process": loader.process_identity(os.getpid()),
            "digest": self.first["archive_sha256"],
            "token": "a" * 32,
        }
        write_json(self.root / "state.json", state)
        selected, token = loader.select(self.root, self.package, self.policy, launch=True)
        self.assertNotEqual(selected, self.first_app)
        self.assertIsNotNone(token)
        self.assertEqual(
            read_json(self.root / "state.json")["trial"]["digest"], second["archive_sha256"]
        )

    def test_client_and_runtime_cannot_advance_past_the_running_application(self):
        second, _archive = self.payload("second", "20261027.130000")
        proof = self.approve(second)
        with patch.dict(os.environ, {"DPE_APPLICATION_VERSION": self.first["version"]}):
            with self.assertRaisesRegex(ValueError, "application update before"):
                runtime.approved_release(
                    proof, self.policy, self.package / "release-signing-key.asc"
                )
            self.assertEqual(
                runtime.approved_release(
                    proof,
                    self.policy,
                    self.package / "release-signing-key.asc",
                    application_update=True,
                )["application"],
                second,
            )

    def test_initial_approved_payload_remains_a_fallback_after_package_upgrade(self):
        self.run_loader("--launch")
        proof = self.approve(self.first)

        def download(url, target, **_kwargs):
            name = (
                "github.json"
                if url.endswith("/latest")
                else "approval.json"
                if "/config/" in url
                else url.rsplit("/", 1)[-1]
            )
            shutil.copyfile(proof / name, target)

        with (
            patch.dict(
                os.environ,
                {
                    "DPE_INSTALL_ROOT": str(self.package),
                    "DPE_CLIENT_OFFLINE": "0",
                    "DPE_CLIENT_PINNED": "0",
                },
            ),
            patch.object(runtime, "download", side_effect=download),
        ):
            self.assertIsNone(application.prepare(self.first_app, self.profile))
        self.assertTrue((self.first_app.parent / "proof/SHA256SUMS.sig").is_file())
        broken, archive = self.payload("broken", "20261027.130000", broken=True)
        updated = {**self.policy, "application": broken}
        write_json(self.package / "bootstrap.json", updated)
        loader.install(self.root, self.package, updated, broken, archive)
        self.assertEqual(self.run_loader("--launch", check=False).returncode, 1)
        self.assertEqual(self.run_loader("--launch").stdout.strip(), "first")

        # An unchanged package pin must not keep reviving a crashing trial.
        self.assertEqual(self.run_loader("--launch").stdout.strip(), "first")

    def test_application_archive_rejects_traversal_links_duplicates_and_extra_files(self):
        for name, mode in (
            ("../escape", stat.S_IFREG),
            ("/escape", stat.S_IFREG),
            ("link", stat.S_IFLNK),
        ):
            with self.subTest(name=name):
                archive = self.directory / "unsafe.zip"
                with zipfile.ZipFile(archive, "w") as output:
                    info = zipfile.ZipInfo(name)
                    info.external_attr = (mode | 0o755) << 16
                    output.writestr(info, b"outside")
                with self.assertRaises(ValueError):
                    loader.extract(archive, self.directory / "unsafe")
        (self.first_app / "injected.py").write_text("untrusted")
        with self.assertRaises(ValueError):
            loader.validate(self.first_app.parent, self.first)

    def test_package_upgrade_reuses_newer_payload_instead_of_downgrading(self):
        self.run_loader("--launch")
        second, archive = self.payload("second", "20261027.140000")
        self.stage(second, archive)
        self.run_loader("--launch")
        middle, _ = self.payload("middle", "20261027.130000")
        updated = copy.deepcopy(self.policy)
        updated["application"] = middle
        write_json(self.package / "bootstrap.json", updated)
        self.assertEqual(self.run_loader("--launch").stdout.strip(), "second")


class BootstrapDownloads(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.target = Path(temporary.name) / "application.zip"
        self.url = (
            f"https://github.com/{loader.REPOSITORY}/releases/download/fixture/application.zip"
        )

    def response(self, data, length=None):
        response = io.BytesIO(data)
        response.headers = {"Content-Length": str(len(data) if length is None else length)}
        return response

    def test_transient_connection_and_http_errors_retry_before_succeeding(self):
        opener = Mock()
        opener.open.side_effect = [
            urllib.error.URLError("connection reset"),
            urllib.error.HTTPError(self.url, 503, "temporarily unavailable", {}, None),
            self.response(b"verified separately"),
        ]
        with (
            patch.object(loader.urllib.request, "build_opener", return_value=opener),
            patch.object(loader.time, "sleep"),
        ):
            loader.download(self.url, self.target)
        self.assertEqual(self.target.read_bytes(), b"verified separately")
        self.assertEqual(opener.open.call_count, 3)

    def test_truncated_response_is_retried_without_concatenating_partial_bytes(self):
        opener = Mock()
        opener.open.side_effect = [self.response(b"partial", 20), self.response(b"complete")]
        with (
            patch.object(loader.urllib.request, "build_opener", return_value=opener),
            patch.object(loader.time, "sleep"),
        ):
            loader.download(self.url, self.target)
        self.assertEqual(self.target.read_bytes(), b"complete")
        self.assertEqual(opener.open.call_count, 2)

    def test_retries_are_bounded_and_failed_downloads_are_removed(self):
        opener = Mock()
        opener.open.side_effect = [self.response(b"partial", 20) for _ in range(3)]
        with (
            patch.object(loader.urllib.request, "build_opener", return_value=opener),
            patch.object(loader.time, "sleep"),
        ):
            with self.assertRaises(loader.http.client.IncompleteRead):
                loader.download(self.url, self.target)
        self.assertFalse(self.target.exists())
        self.assertEqual(opener.open.call_count, 3)

    def test_permanent_http_failure_and_oversize_response_are_not_retried(self):
        for failure in (
            urllib.error.HTTPError(self.url, 404, "missing", {}, None),
            self.response(b"", 64 * 1024 * 1024 + 1),
        ):
            with self.subTest(failure=failure):
                opener = Mock()
                opener.open.side_effect = [failure]
                with (
                    patch.object(loader.urllib.request, "build_opener", return_value=opener),
                    patch.object(loader.time, "sleep") as sleep,
                ):
                    with self.assertRaises((urllib.error.HTTPError, ValueError)):
                        loader.download(self.url, self.target)
                sleep.assert_not_called()
                self.assertEqual(opener.open.call_count, 1)
                self.assertFalse(self.target.exists())
