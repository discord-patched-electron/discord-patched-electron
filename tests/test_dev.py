"""Development storage and profile isolation contracts."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import read_json, write_json
from dev import Manager, copy_changed, disk_bytes, find_profiles, lock, main, name, sync_sidecars
from dev_client import configure_installation, link_helpers


class DevTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.manager = Manager(Path(self.temporary.name) / "dev")

    def test_default_profile_is_reused_and_temporary_is_explicit(self):
        with patch("dev.Manager.run", return_value=0) as run:
            for version in ("one", "two"):
                main(["--home", str(self.manager.home), "run", version])
            self.assertEqual([call.args[1] for call in run.call_args_list], ["dev", "dev"])
        with self.assertRaises(SystemExit):
            main(["run", "one", "--profile", "dev", "--temporary-profile"])

    def test_doctor_scans_requested_external_root(self):
        external = Path(self.temporary.name) / "external"
        profile = external / "check/profile"
        profile.mkdir(parents=True)
        (profile / "Preferences").write_text("fixture")
        with patch("builtins.print") as output:
            self.assertEqual(
                main(
                    [
                        "--home",
                        str(self.manager.home),
                        "doctor",
                        "--root",
                        str(external),
                        "--profiles",
                    ]
                ),
                0,
            )
        self.assertIn(str(profile), output.call_args_list[0].args[0])
        self.assertTrue(profile.exists())

    def test_temporary_profile_cleanup_on_success_and_failure(self):
        persistent = self.manager.profile("dev")
        (persistent / "settings.json").write_text("keep")
        for failure in (False, True):

            def run(label, profile, watch, sync, args, *, failure=failure):
                data = self.manager.profile(profile)
                self.assertNotEqual(data, persistent)
                self.assertEqual(list(data.iterdir()), [])
                (data / "payload").write_text("disposable")
                (self.manager.home / "locks" / (profile + ".profile")).touch()
                if failure:
                    raise ValueError("launch failed")
                return 7

            with patch.object(self.manager, "run", side_effect=run):
                if failure:
                    with self.assertRaisesRegex(ValueError, "launch failed"):
                        self.manager.run_temporary("one", False, False, [])
                else:
                    self.assertEqual(self.manager.run_temporary("one", False, False, []), 7)
            self.assertEqual(list((self.manager.home / "profiles").iterdir()), [persistent])
            self.assertEqual(list((self.manager.home / "locks").iterdir()), [])
        self.assertEqual((persistent / "settings.json").read_text(), "keep")

    def test_storage_counts_hardlinks_once_and_finds_profiles_without_following_links(self):
        root = Path(self.temporary.name)
        profile = self.manager.profile("dev")
        marker = profile / "Preferences"
        marker.write_bytes(b"x" * 8192)
        before = disk_bytes([self.manager.home])
        os.link(marker, profile / "copy")
        self.assertEqual(disk_bytes([self.manager.home]), before)
        (root / "alias").symlink_to(profile, target_is_directory=True)
        nested = profile / "payload"
        nested.mkdir()
        (nested / "Preferences").write_text("not another profile")
        self.assertEqual(list(find_profiles(root)), [profile])

    def test_link_command_pins_manager_version_and_profile(self):
        command = self.manager.link_command("current", "testing")
        self.assertEqual(
            command[2:],
            ["--home", str(self.manager.home), "open", "current", "--profile", "testing", "--"],
        )

    def test_cold_link_activation_uses_normal_manager(self):
        with patch.object(self.manager, "run", return_value=0) as run:
            self.assertEqual(
                self.manager.open_links("current", "testing", ["discord://invite/a"]), 0
            )
        run.assert_called_once_with("current", "testing", False, False, ["discord://invite/a"])

    def test_live_link_activation_joins_only_the_selected_version(self):
        app = self.manager.path("versions", "current")
        app.mkdir()
        write_json(app / "dev-version.json", {})
        profile_lock = self.manager.home / "locks/testing.profile"
        with lock(profile_lock):
            profile_lock.write_text("current")
            with patch("dev.subprocess.call", return_value=0) as call:
                self.assertEqual(
                    self.manager.open_links("current", "testing", ["discord://invite/a"]), 0
                )
            self.assertEqual(call.call_args.args[0], [str(app / "launch.sh"), "discord://invite/a"])
            environment = call.call_args.kwargs["env"]
            self.assertEqual(environment["DPE_PROFILE_DIR"], str(self.manager.profile("testing")))
            self.assertEqual(
                json.loads(environment["DPE_LINK_COMMAND"]),
                self.manager.link_command("current", "testing"),
            )
            profile_lock.write_text("other")
            with self.assertRaisesRegex(ValueError, "different development version"):
                self.manager.open_links("current", "testing", ["discord://invite/a"])

    def test_link_activation_rejects_flags_and_other_schemes(self):
        for args in ([], ["--flag"], ["https://discord.com"], ["discord://invite/a", "--flag"]):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.manager.open_links("current", "testing", args)

    def test_names_cannot_escape_managed_home(self):
        for value in ("../profile", "/tmp/profile", ".", "a/b", "", "a b"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                name(value)

    def test_runtime_shares_bytes_but_resources_are_private(self):
        apps = [self.manager.path("versions", label) for label in ("one", "two")]
        for app in apps:
            (app / "runtime").mkdir(parents=True)
            (app / "resources").mkdir()
            (app / "resources/app.asar").write_bytes(b"private")
            (app / "runtime/electron").write_bytes(b"electron")
            (app / "runtime/electron").chmod(0o755)
            (app / "runtime/resources").symlink_to("../resources")
            self.manager.pool_runtime(app)
        first, second = [app / "runtime/electron" for app in apps]
        self.assertEqual(first.stat().st_ino, second.stat().st_ino)
        self.assertFalse(first.stat().st_mode & 0o222)
        self.assertNotEqual(
            (apps[0] / "resources/app.asar").stat().st_ino,
            (apps[1] / "resources/app.asar").stat().st_ino,
        )
        profile = self.manager.profile("dev")
        (profile / "settings.json").write_text("account stays here")
        self.manager.remove("versions", "one")
        self.assertTrue(second.exists())
        self.assertTrue((profile / "settings.json").exists())
        self.manager.prune(True, False)
        self.assertEqual(len(list((self.manager.home / "objects").iterdir())), 1)
        self.manager.remove("versions", "two")
        self.manager.prune(True, False)
        self.assertEqual(list((self.manager.home / "objects").iterdir()), [])

    def test_published_electron_files_share_pool_without_linking_resources(self):
        app = self.manager.path("versions", "one")
        (app / "runtime").mkdir(parents=True)
        (app / "runtime/electron").write_bytes(b"runtime")
        (app / "runtime/electron").chmod(0o755)
        self.manager.pool_runtime(app)
        profile = self.manager.profile("dev")
        views = (profile / "app-1.0.1", profile / "dpe-updater/installations/view")
        for view in views:
            view.mkdir(parents=True)
            (view / "electron").write_bytes(b"runtime")
            (view / "resources").mkdir()
            (view / "resources/app.asar").write_bytes(b"private")
        self.manager.share_installed_runtime(app, profile)
        for view in views:
            self.assertEqual(
                (view / "electron").stat().st_ino, (app / "runtime/electron").stat().st_ino
            )
            self.assertEqual((view / "resources/app.asar").stat().st_nlink, 1)

    def test_profile_lock_blocks_other_manager_and_cleanup(self):
        self.manager.profile("dev")
        with lock(self.manager.home / "locks/dev.profile"):
            with self.assertRaises(ValueError):
                self.manager.remove("profiles", "dev")
        self.manager.remove("profiles", "dev")

    def test_symlink_entry_refused(self):
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        (self.manager.home / "profiles/external").symlink_to(outside)
        with self.assertRaises(ValueError):
            self.manager.remove("profiles", "external")
        self.assertTrue(outside.exists())

    def test_sync_stages_export_worker_and_redactor_without_updater_transactions(self):
        root = Path(self.temporary.name)
        repository = Path(__file__).resolve().parents[1]
        source = root / "source"
        helpers = (
            "export-logs.cjs",
            "export_logs.py",
            "log_redaction.py",
            "diagnostic_log.py",
            "diagnostic_inventory.py",
        )
        for filename in helpers:
            target = source / "integration/updater" / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(repository / "integration/updater" / filename, target)
        (source / "integration/updater/client.py").write_text("must not sync")
        (source / "integration/updater/future_worker.py").write_text("must not sync")
        app = self.manager.path("versions", "fixture")
        app.mkdir()
        (app / "integration/updater").mkdir(parents=True)
        (app / "integration/updater/client.py").write_text("frozen")
        bundle = root / "bundle"
        bundle.mkdir()
        for filename in ("config-app.js", "ui-runtime.cjs", "PREACT-LICENSE.txt"):
            (bundle / filename).write_text("fixture")
        with (
            patch("dev.ROOT", source),
            patch("build_ui.build", return_value=bundle),
            patch("patch_app_audio.SIDECARS", {}),
        ):
            self.manager.sync(app)
        for filename in helpers:
            self.assertEqual(
                (app / "integration/updater" / filename).read_bytes(),
                (source / "integration/updater" / filename).read_bytes(),
            )
        self.assertEqual((app / "integration/updater/client.py").read_text(), "frozen")
        self.assertFalse((app / "integration/updater/future_worker.py").exists())
        profile = root / "profile"
        (profile / "logs").mkdir(parents=True)
        (profile / "logs/renderer.log").write_text(
            "renderer error\nAuthorization: Bearer PRIVATE_FIXTURE"
        )
        output = root / "export.zip"
        subprocess.run(
            [sys.executable, str(app / "integration/updater/export_logs.py"), str(output)],
            input=json.dumps(
                {
                    "profile": str(profile),
                    "crashPaths": [],
                    "metadata": {},
                    "executable": sys.executable,
                }
            ),
            text=True,
            env={**os.environ, "PATH": ""},
            check=True,
            capture_output=True,
        )

        with zipfile.ZipFile(output) as archive:
            text = archive.read("profile/logs/renderer.log").decode()
            self.assertIn("renderer error", text)
            self.assertNotIn("PRIVATE_FIXTURE", text)

    def test_sync_replaces_links_without_mutating_shared_source(self):
        root = Path(self.temporary.name)
        source, destination, other = root / "source", root / "destination", root / "other"
        source.write_text("new")
        other.write_text("old")
        destination.symlink_to(other)
        copy_changed(source, destination)
        self.assertEqual(other.read_text(), "old")
        self.assertEqual(destination.read_text(), "new")
        before = destination.stat().st_mtime_ns
        copy_changed(source, destination)
        self.assertEqual(destination.stat().st_mtime_ns, before)

    def test_editable_sidecars_keep_node_resolution_and_picker_rpc_working(self):
        app = self.manager.path("versions", "fixture")
        audio = app / "integration/audio"
        audio.mkdir(parents=True)
        (audio / "app-audio.cjs").write_text("module.exports = {};\n")
        voice = app / "voice"
        voice.mkdir()
        root = Path(__file__).resolve().parents[1]
        sidecars = {
            "../video/capture-backends.cjs": "dpe-capture-backends.cjs",
            "../video/settings.cjs": "dpe-screenshare-settings.cjs",
            "../video/capture-picker.cjs": "dpe-capture-picker.cjs",
        }
        for source in sidecars:
            target = audio / source
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / "integration/audio" / source, target)
        # Migrate the previous overlay layout, which Node resolves into video/.
        for source, destination in sidecars.items():
            (voice / destination).symlink_to((audio / source).resolve())
        sync_sidecars(app, sidecars)
        link_helpers(app, voice, sidecars)
        profile = app / "profile"
        profile.mkdir()
        write_json(
            profile / "settings.json",
            {"openasar": {"screensharing": {"experimentalCapture": True}}},
        )
        script = r"""
const assert = require('node:assert/strict');
const {createRequire} = require('node:module');
const Module = require('node:module');
const {EventEmitter} = require('node:events');
const path = require('node:path');
const app = process.argv[1];
process.env.DPE_APP_ROOT = app;
process.env.DPE_PROFILE_DIR = path.join(app, 'profile');
const load = createRequire(path.join(app, 'voice/index.js'));
const entry = load.resolve('./dpe-capture-backends.cjs');
assert.equal(createRequire(entry).resolve('./dpe-screenshare-settings.cjs'),
  load.resolve('./dpe-screenshare-settings.cjs'));
assert.equal(createRequire(entry).resolve('./dpe-capture-picker.cjs'),
  load.resolve('./dpe-capture-picker.cjs'));
const ipc = new EventEmitter();
ipc.invoke = async (channel, request) => {
  if (channel === 'dpe-capture-methods') return ['vkcapture'];
  assert.equal(channel, 'dpe-capture-picker');
  assert.equal(request.op, 'list');
  return {enabled: true, sources: []};
};
const original = Module._load;
Module._load = function(request, ...args) {
  if (request === 'electron') return {ipcRenderer: ipc};
  if (request === './dpe-capture-picker.cjs') return {load: () => ({})};
  return original.call(this, request, ...args);
};
global.window = new EventTarget();
load('./dpe-capture-backends.cjs').install(path.join(app, 'voice'), {});
const timeout = setTimeout(() => { throw Error('Picker RPC did not reply'); }, 1000);
window.addEventListener('dpe-capture-picker-reply', event => {
  const reply = JSON.parse(event.detail);
  assert.equal(reply.id, '00000000-0000-0000-0000-000000000001');
  assert.deepEqual(reply.value, {enabled: true, sources: []});
  assert.equal(reply.error, undefined);
  clearTimeout(timeout);
});
window.dispatchEvent(new CustomEvent('dpe-capture-picker-request', {
  detail: JSON.stringify({id: '00000000-0000-0000-0000-000000000001', op: 'list'})
}));
"""
        subprocess.run(["node", "-e", script, str(app)], check=True, timeout=10)
        # Watch/restart must refresh aliases without changing their link identity.
        settings = audio / "../video/settings.cjs"
        settings.write_text(settings.read_text() + "\n// Edited helper\n")
        before = (voice / "dpe-screenshare-settings.cjs").readlink()
        sync_sidecars(app, sidecars)
        link_helpers(app, voice, sidecars)
        self.assertEqual((voice / "dpe-screenshare-settings.cjs").readlink(), before)
        self.assertEqual(
            (voice / "dpe-screenshare-settings.cjs").read_bytes(), settings.read_bytes()
        )
        subprocess.run(["node", "-e", script, str(app)], check=True, timeout=10)

    def test_installed_view_migrates_helper_links_and_reuses_content_edits(self):
        app = self.manager.path("versions", "fixture")
        updater = app / "integration/updater"
        updater.mkdir(parents=True)
        root = Path(__file__).resolve().parents[1]
        for filename in ("installation.py", "client_mods.py"):
            shutil.copy2(root / "integration/updater" / filename, updater / filename)
        (updater / "client.py").write_text("def compatibility_key():\n    return 'fixture'\n")
        self.manager.freeze_client(app)
        profile = self.manager.profile("fixture")
        host = app / "host"
        voice = host / "modules/discord_voice"
        voice.mkdir(parents=True)
        (host / "resources").mkdir()
        (app / "resources").mkdir()
        runtime = app / "runtime"
        runtime.mkdir()
        (runtime / "electron").write_bytes(b"runtime")
        (app / "libdpe-camera-probe.so").write_bytes(b"helper")
        (app / "resources/app.asar").write_bytes(b"fixture")
        write_json(host / "resources/build_info.json", {"version": "1.0.1"})
        state = {
            "channel": "canary",
            "version": "1.0.1",
            "modules": {"discord_voice": {"version": 1}},
        }
        write_json(host / "client.json", state)
        old_helper, new_helper = app / "old.cjs", app / "packaged.cjs"
        old_helper.write_text("same bytes")
        new_helper.write_text("same bytes")
        link = voice / "dpe-helper.cjs"
        link.symlink_to(old_helper)

        def load():
            sys.path.insert(0, str(root / "integration/updater"))
            try:
                namespace = {"__file__": str(updater / "installation.py")}
                exec(
                    compile(
                        (updater / "installation.py").read_text(), namespace["__file__"], "exec"
                    ),
                    namespace,
                )
                return namespace
            finally:
                sys.path.pop(0)

        prepare = load()["prepare_view"]
        previous = prepare(app, profile, host, runtime / "electron", state)
        link.unlink()
        link.symlink_to(new_helper)
        self.assertEqual(prepare(app, profile, host, runtime / "electron", state), previous)
        configure_installation(app)
        before = (updater / "installation.py").read_bytes()
        configure_installation(app)
        self.assertEqual((updater / "installation.py").read_bytes(), before)
        installation = load()
        prepare = installation["prepare_view"]
        current = prepare(app, profile, host, runtime / "electron", state)
        self.assertNotEqual(current, previous)
        installed = current / "modules/discord_voice-1/discord_voice/dpe-helper.cjs"
        self.assertEqual(installed.resolve(), new_helper)
        published = installation["publish"](app, profile, host, runtime / "electron")
        self.assertEqual(
            (published / "modules/discord_voice-1/discord_voice/dpe-helper.cjs").resolve(),
            new_helper,
        )
        self.assertEqual(
            (previous / "modules/discord_voice-1/discord_voice/dpe-helper.cjs").resolve(),
            old_helper,
        )
        new_helper.write_text("edited helper")
        self.assertEqual(prepare(app, profile, host, runtime / "electron", state), current)
        self.assertEqual(installed.read_text(), "edited helper")

    def test_create_failure_cleans_partial_version(self):
        with self.assertRaises((OSError, ValueError)):
            self.manager.create("failed", Path("/missing"), Path("/missing"))
        self.assertFalse(self.manager.path("versions", "failed").exists())

    def test_development_identity_tracks_links_and_keeps_native_content_hashes(self):
        app = self.manager.path("versions", "fixture")
        updater = app / "integration/updater"
        updater.mkdir(parents=True)
        (updater / "client.py").write_text("def compatibility_key():\n    return 'native-base'\n")
        installation = updater / "installation.py"
        installation.write_text(
            "import os, hashlib\nfrom pathlib import Path\n"
            "def checksum(item): return hashlib.sha256(item.read_bytes()).hexdigest()\n"
            "def manifest(directory):\n    files = {}\n"
            "    for item in directory.iterdir():\n"
            "        relative = item.name\n        if item.is_file():\n"
            '            files[relative] = {"New": {"Sha256": checksum(item)}}\n'
            "    return files\n"
        )
        self.manager.freeze_client(app)
        namespace = {}
        exec(installation.read_text(), namespace)
        data = app / "data"
        data.mkdir()
        helper = app / "helper"
        helper.write_text("old")
        (data / "helper").symlink_to(helper)
        native = data / "native.node"
        native.write_bytes(b"native")
        before = namespace["manifest"](data)
        helper.write_text("edited")
        self.assertEqual(namespace["manifest"](data), before)
        native.write_bytes(b"changed native")
        self.assertNotEqual(namespace["manifest"](data), before)
        result = subprocess.check_output(
            [
                sys.executable,
                "-c",
                "import sys; sys.path.insert(0, sys.argv[1]); "
                "import client; print(client.compatibility_key())",
                str(updater),
            ],
            text=True,
        )
        self.assertEqual(result.strip(), "native-base")

    def test_run_forwards_flags_and_profile(self):
        app = self.manager.path("versions", "one")
        app.mkdir()
        write_json(app / "dev-version.json", {"schema": 1})
        # Exercise argument parsing independently: unknown Electron flags are
        # only accepted after the explicit separator.
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "scripts/dev.py"),
                "--home",
                str(self.manager.home),
                "run",
                "missing",
                "--profile",
                "other",
                "--",
                "--ozone-platform=x11",
            ],
            capture_output=True,
            text=True,
        )
        self.assertIn("Unknown version", result.stderr)
        self.assertEqual(read_json(app / "dev-version.json")["schema"], 1)


if __name__ == "__main__":
    unittest.main()
