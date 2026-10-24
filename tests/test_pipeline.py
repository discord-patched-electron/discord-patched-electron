import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from assemble import validate_port
from common import (
    ROOT,
    arch_version,
    electron_channel,
    package_name,
    read_json,
    sha256,
    version_key,
    write_json,
)
from fingerprints import integration_key, runtime_key
from lock_clients import normalize
from prepare_app import patch_bundle, replace


class SourceLocks(unittest.TestCase):
    def test_dependency_followups_are_checksum_pinned(self):
        sources = read_json(ROOT / "config/sources.lock.json")
        port = copy.deepcopy(sources["ports"]["46"])
        port["followup_patches"][0]["patch_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "Follow-up compatibility patch"):
            validate_port(port, ROOT / sources["fork"]["patch"])

    def test_port_exclusions_are_fully_accounted_for(self):
        sources = read_json(ROOT / "config/sources.lock.json")
        for port in sources["ports"].values():
            validate_port(port, ROOT / sources["fork"]["patch"])
            changed = copy.deepcopy(port)
            changed["replaced_paths"].append("discord/*")
            with self.assertRaises(ValueError):
                validate_port(changed, ROOT / sources["fork"]["patch"])
            changed = copy.deepcopy(port)
            changed["upstream_equivalents"].pop(next(iter(changed["upstream_equivalents"])))
            with self.assertRaises(ValueError):
                validate_port(changed, ROOT / sources["fork"]["patch"])

    def test_vendored_files_match_exact_upstream(self):
        provenance = read_json(ROOT / "packaging/upstream/provenance.json")
        for name, expected in provenance["files"].items():
            self.assertEqual(sha256(ROOT / "packaging/upstream" / name), expected)

    def test_patch_matches_lock(self):
        fork = read_json(ROOT / "config/sources.lock.json")["fork"]
        self.assertEqual(sha256(ROOT / fork["patch"]), fork["patch_sha256"])

    def test_three_distinct_names(self):
        names = {package_name(d, e) for d in ("stable", "ptb", "canary") for e in ("prerelease",)}
        self.assertEqual(len(names), 3)
        self.assertIn("discord-canary-prerelease-patched-electron-bin", names)
        with self.assertRaises(ValueError):
            package_name("../../bad", "stable")

    def test_atomic_json_does_not_touch_unchanged_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lock.json"
            write_json(path, {"b": 2, "a": 1})
            before = path.stat().st_mtime_ns
            write_json(path, {"a": 1, "b": 2})
            self.assertEqual(path.stat().st_mtime_ns, before)


class Manifests(unittest.TestCase):
    def fixture(self):
        entry = {
            "host_version": [1, 0, 123],
            "package_sha256": "a" * 64,
            "url": "https://ptb.dl2.discordapp.net/distro/app/ptb/linux/x64/1.0.123/full.distro",
        }
        modules = {}
        for name in ("discord_voice", "discord_krisp", "discord_desktop_core"):
            module = {
                **entry,
                "module_version": 2,
                "url": entry["url"].replace("/full.distro", f"/{name}/2/full.distro"),
            }
            modules[name] = {"full": module}
        return {"full": entry, "modules": modules}

    def test_uses_manifest_module_revision(self):
        result = normalize("ptb", self.fixture())
        self.assertEqual(result["sources"]["discord_voice"]["module_version"], 2)
        self.assertEqual(result["channel"], "ptb")

    def test_rejects_mixed_channel(self):
        with self.assertRaises(ValueError):
            normalize("stable", self.fixture())

    def test_rejects_mixed_host_version(self):
        manifest = self.fixture()
        manifest["modules"]["discord_krisp"]["full"]["host_version"] = [1, 0, 122]
        with self.assertRaises(ValueError):
            normalize("ptb", manifest)

    def test_krisp_required(self):
        manifest = self.fixture()
        del manifest["modules"]["discord_krisp"]
        with self.assertRaises(ValueError):
            normalize("ptb", manifest)


class VersionsAndKeys(unittest.TestCase):
    def test_semver_numeric_and_promotion(self):
        versions = ["45.0.0", "45.0.0-beta.10", "45.0.0-beta.2", "45.0.0-alpha.1", "44.99.99"]
        self.assertEqual(sorted(versions, key=version_key), list(reversed(versions)))
        self.assertEqual(electron_channel("47.0.0-nightly.20261023"), "nightly")

    def test_arch_order_matches_semver(self):
        import shutil

        if not shutil.which("vercmp"):
            self.skipTest("Arch vercmp is not installed on this host")
        values = ["45.0.0-alpha.1", "45.0.0-beta.2", "45.0.0-beta.10", "45.0.0"]
        for older, newer in zip(values[:-1], values[1:], strict=True):
            result = subprocess.check_output(
                ["vercmp", arch_version(older), arch_version(newer)], text=True
            )
            self.assertLess(int(result), 0)

    def test_discord_changes_reuse_runtime_but_invalidate_integration(self):
        sources = read_json(ROOT / "config/sources.lock.json")
        clients = read_json(ROOT / "config/clients.lock.json")["clients"]
        target = sources["targets"]["prerelease"]
        runtime = runtime_key(target, sources, {"compiler": "pinned-example"})
        before = integration_key(runtime, clients["stable"], "prerelease")
        changed = copy.deepcopy(clients["stable"])
        changed["sources"]["discord_krisp"]["sha256"] = "b" * 64
        self.assertNotEqual(before, integration_key(runtime, changed, "prerelease"))
        self.assertEqual(runtime, runtime_key(target, sources, {"compiler": "pinned-example"}))
        self.assertNotEqual(before, integration_key(runtime, clients["canary"], "prerelease"))

    def test_changed_compile_inputs_rebuild(self):
        sources = read_json(ROOT / "config/sources.lock.json")
        target = sources["targets"]["prerelease"]
        self.assertNotEqual(
            runtime_key(target, sources, {"gn": "a"}), runtime_key(target, sources, {"gn": "b"})
        )
        changed = copy.deepcopy(sources)
        changed["ports"]["46"]["candidate_tree"] = "f" * 40
        self.assertNotEqual(runtime_key(target, sources, {}), runtime_key(target, changed, {}))

    def test_validation_evidence_does_not_invalidate_native_bytes(self):
        sources = read_json(ROOT / "config/sources.lock.json")
        target = sources["targets"]["prerelease"]
        changed = copy.deepcopy(sources)
        changed["ports"]["46"]["status"] = "runtime-validated"
        self.assertEqual(runtime_key(target, sources, {}), runtime_key(target, changed, {}))


class BootstrapPatches(unittest.TestCase):
    def test_package_owned_host_ignores_manual_update_gate_without_rewriting_version(self):
        source = (
            'resourcesPath=path_1.default.join(require.main.filename,"..",".."),'
            'process.resourcesPath;skipHostUpdate=settings?.get("SKIP_HOST_UPDATE")||!hostUpdatable;'
            "Exec=${exePath};Name=${appName};Icon=${iconPath}"
        )
        patched = patch_bundle(source, "test-package", Path('/tmp/test "quoted" app'))
        self.assertIn(
            'skipHostUpdate=locallyInstalledModules||settings?.get("SKIP_HOST_UPDATE")||!hostUpdatable',
            patched,
        )
        self.assertIn(json.dumps('/tmp/test "quoted" app/resources'), patched)
        self.assertNotIn("buildInfo.version=", patched)
        with self.assertRaisesRegex(ValueError, "Unsupported bootstrap"):
            patch_bundle(source.replace("skipHostUpdate=", "newHostUpdate="), "test-package")

    def test_exact_edit_and_unknown_layout(self):
        self.assertEqual(replace("a X b", "X", "Y"), "a Y b")
        with self.assertRaises(ValueError):
            replace("a X X b", "X", "Y")
        with self.assertRaises(ValueError):
            patch_bundle("unsupported bundle", "discord-stable-stable-patched-electron-bin")


if __name__ == "__main__":
    unittest.main()
