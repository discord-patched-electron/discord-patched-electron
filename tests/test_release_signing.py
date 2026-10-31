"""Real OpenPGP signatures reject modified packages, checksum lists and keys."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_releases as release_fixtures

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import releases
from common import digest, sha256, write_json
from release_signing import sign, verify


@unittest.skipUnless(shutil.which("gpg") and shutil.which("gpgv"), "GnuPG required")
class ReleaseSigning(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="signing-fixture-")
        cls.home = Path(cls.temporary.name)
        command = ["gpg", "--batch", "--homedir", str(cls.home)]
        subprocess.run(
            [
                *command,
                "--pinentry-mode",
                "loopback",
                "--passphrase",
                "",
                "--quick-generate-key",
                "Disposable signing fixture",
                "ed25519",
                "sign",
                "1d",
            ],
            check=True,
            capture_output=True,
        )
        cls.secret = subprocess.check_output([*command, "--armor", "--export-secret-keys"])
        cls.public = cls.home / "public.asc"
        cls.public.write_bytes(subprocess.check_output([*command, "--armor", "--export"]))

    @classmethod
    def tearDownClass(cls):
        subprocess.run(["gpgconf", "--homedir", str(cls.home), "--kill", "gpg-agent"], check=True)
        cls.temporary.cleanup()

    def checksums(self, directory):
        manifest = json.loads((directory / "release.json").read_text())
        (directory / "SHA256SUMS").write_text(
            "".join(
                f"{sha256(directory / name)}  {name}\n"
                for name in [*manifest["assets"], "release.json"]
            )
        )

    def release_fixture(self, directory):
        fixture = release_fixtures.ReleaseApproval()
        fixture.setUp()
        directory.mkdir(parents=True)
        assets = {}
        for ext in releases.FORMATS:
            name = "app" + ext
            (directory / name).write_bytes(b"synthetic package")
            assets[name] = sha256(directory / name)
        manifest = {
            **fixture.manifest,
            "assets": assets,
            "signing_required": True,
            "workflow_run": "23",
            "minimum_glibc": "2.35",
            "runtime": {
                "version": "46.0.0-alpha.1",
                "cache_release": "runtime-cache-" + "c" * 64,
                "archive_sha256": "d" * 64,
                "capacity_sha256": digest({"synthetic": True}),
                "run": "42",
                "compiler_commit": "e" * 40,
            },
        }
        write_json(directory / "release.json", manifest)
        self.checksums(directory)
        sign(directory, self.secret)
        return fixture, manifest

    def test_signed_checksums_bind_manifest_and_non_arch_assets(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("release_signing.PUBLIC_KEY", self.public),
        ):
            directory = Path(temporary) / "release"
            _fixture, manifest = self.release_fixture(directory)
            verify(directory)
            # A stale signature/list can survive a partial copy or local edit.
            # The manifest's own checksums alone cannot authenticate that edit.
            (directory / "app.deb").write_bytes(b"different synthetic package")
            manifest["assets"]["app.deb"] = sha256(directory / "app.deb")
            write_json(directory / "release.json", manifest)
            releases.verify_assets(directory, manifest)
            with self.assertRaisesRegex(ValueError, "checksum|signed"):
                verify(directory)

    def test_approval_rejects_a_manifest_not_covered_by_the_signed_list(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("release_signing.PUBLIC_KEY", self.public),
        ):
            root = Path(temporary)
            tag = releases.release_tag("1.0.2365", "b" * 64)
            directory = root / "out/approve-release" / tag
            fixture, manifest = self.release_fixture(directory)
            manifest["minimum_glibc"] = "2.36"
            write_json(directory / "release.json", manifest)
            write_json(
                root / "config/update-policy.json",
                {"last_good_runtime": {"version": "46.0.0-alpha.1"}},
            )
            write_json(directory / "runtime/capacity.json", {"synthetic": True})
            release = {
                **fixture.release,
                "assets": [
                    {"name": file.name, "digest": "sha256:" + sha256(file)}
                    for file in directory.iterdir()
                    if file.is_file()
                ],
            }

            def api(endpoint, **kwargs):
                if endpoint == "releases/tags/" + tag:
                    return release
                if endpoint == "actions/runs/23":
                    return fixture.workflow
                if endpoint == "commits/" + tag:
                    return {"sha": manifest["source_commit"]}
                if endpoint == "releases/tags/" + manifest["runtime"]["cache_release"]:
                    self.assertEqual(kwargs["repository"], releases.CACHE_REPOSITORY)
                    return {
                        "draft": False,
                        "assets": [{"name": "runtime-dist.zip", "digest": "sha256:" + "d" * 64}],
                    }
                if endpoint == "actions/runs/42":
                    return {"conclusion": "success", "head_sha": "e" * 40}
                raise AssertionError(endpoint)

            with (
                patch.object(releases, "ROOT", root),
                patch.object(releases, "api", side_effect=api),
                patch.object(releases, "run") as run,
                self.assertRaisesRegex(ValueError, "checksum|signed"),
            ):
                releases.approve(tag, check_only=True)
            # No mocked command is allowed to mutate remote state.
            self.assertTrue(
                all(call.args[:3] == ("gh", "release", "download") for call in run.call_args_list)
            )

    def test_valid_signature_cannot_authorize_incomplete_or_ambiguous_checksums(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("release_signing.PUBLIC_KEY", self.public),
        ):
            directory = Path(temporary) / "release"
            self.release_fixture(directory)
            sums = directory / "SHA256SUMS"
            original = sums.read_text()
            rows = original.splitlines(keepends=True)
            cases = [
                "".join(row for row in rows if not row.endswith("release.json\n")),
                "".join(row for row in rows if not row.endswith("app.deb\n")),
                original + rows[0],
                original + "a" * 64 + "  ../outside\n",
                original.replace(
                    sha256(directory / "app.deb") + "  app.deb", "a" * 64 + "  app.deb"
                ),
            ]
            for content in cases:
                with self.subTest(content=content):
                    sums.write_text(content)
                    subprocess.run(
                        [
                            "gpg",
                            "--batch",
                            "--yes",
                            "--homedir",
                            str(self.home),
                            "--pinentry-mode",
                            "loopback",
                            "--passphrase",
                            "",
                            "--detach-sign",
                            "--output",
                            str(sums) + ".sig",
                            str(sums),
                        ],
                        check=True,
                        capture_output=True,
                    )
                    with self.assertRaisesRegex(ValueError, "checksum"):
                        verify(directory)

    def test_sign_verify_retry_and_reject_tampering(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("release_signing.PUBLIC_KEY", self.public),
        ):
            directory = Path(temporary)
            (directory / "release.json").write_text(
                json.dumps({"assets": {"app.pkg.tar.zst": "unused"}})
            )
            (directory / "app.pkg.tar.zst").write_bytes(b"package bytes")
            write_json(
                directory / "release.json",
                {"assets": {"app.pkg.tar.zst": sha256(directory / "app.pkg.tar.zst")}},
            )
            self.checksums(directory)
            sign(directory, self.secret)
            verify(directory)
            before = (directory / "app.pkg.tar.zst.sig").read_bytes()
            sign(directory, self.secret)
            self.assertEqual(before, (directory / "app.pkg.tar.zst.sig").read_bytes())
            for name in ("app.pkg.tar.zst", "SHA256SUMS", "release-signing-key.asc"):
                path = directory / name
                original = path.read_bytes()
                path.write_bytes(b"changed")
                with (
                    self.subTest(name=name),
                    self.assertRaises((ValueError, subprocess.CalledProcessError)),
                ):
                    verify(directory)
                path.write_bytes(original)
            (directory / "SHA256SUMS.sig").unlink()
            with self.assertRaisesRegex(ValueError, "missing"):
                verify(directory, required=False)

    def test_interrupted_signing_can_retry_without_partial_new_signatures(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("release_signing.PUBLIC_KEY", self.public),
        ):
            directory = Path(temporary) / "release"
            self.release_fixture(directory)
            names = ("app.pkg.tar.zst.sig", "SHA256SUMS.sig", "release-signing-key.asc")
            for name in names:
                (directory / name).unlink()
            run = subprocess.run

            def interrupt(command, **kwargs):
                if "--detach-sign" in command and command[-1].endswith("SHA256SUMS"):
                    raise subprocess.CalledProcessError(1, command)
                return run(command, **kwargs)

            with (
                patch("release_signing.subprocess.run", side_effect=interrupt),
                self.assertRaises(subprocess.CalledProcessError),
            ):
                sign(directory, self.secret)
            self.assertFalse(any((directory / name).exists() for name in names))
            sign(directory, self.secret)
            verify(directory)

    def test_retry_preserves_valid_signatures_from_partial_publication(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("release_signing.PUBLIC_KEY", self.public),
        ):
            directory = Path(temporary) / "release"
            self.release_fixture(directory)
            signature = directory / "app.pkg.tar.zst.sig"
            original = signature.read_bytes()
            (directory / "SHA256SUMS.sig").unlink()
            (directory / "release-signing-key.asc").unlink()
            sign(directory, self.secret)
            self.assertEqual(signature.read_bytes(), original)
            verify(directory)

            # Resuming must never replace an invalid existing signature.
            (directory / "SHA256SUMS.sig").unlink()
            signature.write_bytes(b"damaged signature")
            with self.assertRaises(subprocess.CalledProcessError):
                sign(directory, self.secret)
            self.assertEqual(signature.read_bytes(), b"damaged signature")
            self.assertFalse((directory / "SHA256SUMS.sig").exists())

    def test_retry_after_each_interrupted_signature_publication(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("release_signing.PUBLIC_KEY", self.public),
        ):
            directory = Path(temporary) / "release"
            self.release_fixture(directory)
            names = ("app.pkg.tar.zst.sig", "SHA256SUMS.sig", "release-signing-key.asc")
            link = os.link
            for boundary in range(len(names)):
                with self.subTest(boundary=boundary):
                    for name in names:
                        (directory / name).unlink()
                    calls = 0

                    def interrupt(source, target, *, boundary=boundary):
                        nonlocal calls
                        if calls == boundary:
                            raise OSError("injected publication failure")
                        calls += 1
                        return link(source, target)

                    with (
                        patch("release_signing.os.link", side_effect=interrupt),
                        self.assertRaisesRegex(OSError, "injected"),
                    ):
                        sign(directory, self.secret)
                    before = {
                        name: (directory / name).read_bytes()
                        for name in names
                        if (directory / name).exists()
                    }
                    self.assertEqual(len(before), boundary)
                    sign(directory, self.secret)
                    verify(directory)
                    self.assertEqual(
                        before, {name: (directory / name).read_bytes() for name in before}
                    )

    def test_unsigned_legacy_is_allowed_only_when_explicitly_requested(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "release.json").write_text(
                json.dumps({"assets": {"app.pkg.tar.zst": "unused"}})
            )
            verify(directory, required=False)
            with self.assertRaisesRegex(ValueError, "missing"):
                verify(directory)
