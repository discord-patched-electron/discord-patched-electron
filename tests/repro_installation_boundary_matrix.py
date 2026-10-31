"""Offline installation fault coverage, not a claimed defect reproduction.

Reuse NativeInstallation and official-loader-shaped mod_fixture data. Nothing
executes the fixture runtime, loader or mod payload. A fork exits at a selected
boundary to avoid accidentally exercising exception recovery in crash cases.
"""

import errno
import os
import signal
import sqlite3
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

import test_installation as fixtures
from mod_fixture import install

installation = fixtures.installation


@contextmanager
def boundaries(fixture, callback):
    """Observe real operations; inject only at the chosen before/after boundary."""
    counts = {}

    def wrap(label, operation, *args, **kwargs):
        if label is None:
            return operation(*args, **kwargs)
        counts[label] = counts.get(label, 0) + 1
        label = f"{label}:{counts[label]}"
        callback(label, "before")
        result = operation(*args, **kwargs)
        callback(label, "after")
        return result

    original_connect = sqlite3.connect

    class Connection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            label = None
            if sql in ("BEGIN IMMEDIATE", "COMMIT", "ROLLBACK"):
                label = sql
            elif sql.startswith("INSERT"):
                label = "db:" + (parameters[0] if "REPLACE" in sql else "install_id")
            return wrap(label, super().execute, sql, parameters)

        def backup(self, target, **kwargs):
            return wrap("db:backup", super().backup, target, **kwargs)

    def connect(*args, **kwargs):
        return original_connect(*args, factory=Connection, **kwargs)

    def path_label(path, method):
        if path.name == "activation.json":
            return "journal:" + method
        if path.parent == fixture.profile and (
            path.name.startswith("app-") or path.name == "DiscordCanary"
        ):
            return "public:" + path.name + ":" + method
        if path.name == "incoming":
            return "incoming:" + method
        if method == "replace" and ".dpe-" in path.name:
            return "entry:replace"
        if method == "rename" and "backups" in path.parts:
            return "restore:" + path.name
        if method == "write_bytes" and path.parent.parent.name == "incoming":
            return "incoming:mod-write"
        return None

    def path_wrapper(method):
        original = getattr(Path, method)

        def call(path, *args, **kwargs):
            label = path_label(path, method)
            if method == "replace" and Path(args[0]).name == "activation.json":
                label = "journal:replace"
            return wrap(label, original, path, *args, **kwargs)

        return call

    original_sync = installation.sync_directory
    original_write = installation.write_json
    original_copy = installation.copy_payload
    original_run = installation.subprocess.run

    def run(command, **kwargs):
        label = "sync:filesystem" if command[:2] == ["sync", "-f"] else None
        return wrap(label, original_run, command, **kwargs)

    def sync(directory):
        name = "profile" if directory == fixture.profile else "private"
        if "backups" in directory.parts:
            name = "backup"
        return wrap("sync:" + name, original_sync, directory)

    def write(path, value):
        label = "journal:write" if path.name == "activation.json" else None
        return wrap(label, original_write, path, value)

    def copy(source, destination):
        label = "incoming:copy" if destination.parent.name == "incoming" else None
        return wrap(label, original_copy, source, destination)

    with ExitStack() as stack:
        stack.enter_context(patch.object(sqlite3, "connect", connect))
        stack.enter_context(patch.object(installation, "sync_directory", sync))
        stack.enter_context(patch.object(installation, "write_json", write))
        stack.enter_context(patch.object(installation, "copy_payload", copy))
        stack.enter_context(patch.object(installation.subprocess, "run", run))
        for method in ("rename", "replace", "unlink", "write_bytes"):
            stack.enter_context(patch.object(Path, method, path_wrapper(method)))
        yield


class InstallationBoundaryMatrix(unittest.TestCase):
    @contextmanager
    def fixture(self, upgrade, mod="Equicord"):
        fixture = fixtures.NativeInstallation()
        fixture.setUp()
        try:
            fixture.stock()
            public = fixture.publish()
            target = fixture.profile / ("patcher.js" if mod == "Vencord" else "desktop.asar")
            target.write_bytes(b"synthetic mod self-update v2")
            loader = install(public / "resources", mod, target=str(target))
            settings = fixture.profile / "settings.json"
            settings.write_bytes(b'{"openasar":{"css":"synthetic"}}')
            mod_settings = fixture.profile / "mod-settings.json"
            mod_settings.write_bytes(b'{"autoUpdate":true,"theme":"synthetic"}')
            before = fixture.database()
            fixture.exe.write_text("replacement runtime")
            (fixture.app / "resources/app.asar").write_text("replacement bootstrap")
            if upgrade:
                fixture.advance(version="1.0.10", revision=8)
            installation.prepare_view(
                fixture.app, fixture.profile, fixture.host, fixture.exe, fixture.state
            )
            yield fixture, before, loader, target, (settings, mod_settings)
        finally:
            fixture.doCleanups()

    def crash(self, action):
        """Bound each child to 15 seconds, requiring the deliberate exit status."""
        child = os.fork()
        if child == 0:
            signal.alarm(15)
            try:
                action()
            except BaseException:
                os._exit(91)
            os._exit(92)  # The requested crash boundary was never reached.
        _, status = os.waitpid(child, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 90)

    def recover(self, fixture):
        with sqlite3.connect(fixture.profile / "installer.db") as connection:
            installation.recover(fixture.profile, connection)

    def verify(self, fixture, before, loader, target, settings):
        current = fixture.database()
        active = current["dpe/active/canary"]
        committed = active["activation"] != before["dpe/active/canary"]["activation"]
        public = fixture.profile / ("app-" + active["version"])
        self.assertEqual(
            (public / "electron").read_text(),
            "replacement runtime" if committed else "fork runtime",
        )
        self.assertEqual((public / "resources/app.asar").read_bytes(), loader)
        self.assertEqual(
            (public / "resources/_app.asar").read_text(),
            "replacement bootstrap" if committed else "project loader",
        )
        self.assertEqual((fixture.profile / "DiscordCanary").resolve(), public / "DiscordCanary")
        for key in ("install_id", "latest/host/app/canary/linux/x64"):
            self.assertEqual(current[key], before[key])
        self.assertEqual(
            current["host/app/canary/linux/x64"][0], before["host/app/canary/linux/x64"][0]
        )
        if not committed:
            self.assertEqual(current, before)
        else:
            records = current["host/app/canary/linux/x64"]
            self.assertEqual(records[-1], installation.record(public, fixture.state))
        self.assertEqual(target.read_bytes(), b"synthetic mod self-update v2")
        self.assertEqual(settings[0].read_bytes(), b'{"openasar":{"css":"synthetic"}}')
        self.assertEqual(settings[1].read_bytes(), b'{"autoUpdate":true,"theme":"synthetic"}')
        self.assertFalse((fixture.profile / "dpe-updater/activation.json").exists())
        # This is the installation phase of a subsequent launch, not code execution.
        installed = fixture.publish()
        self.assertEqual((installed / "electron").read_text(), "replacement runtime")
        self.assertEqual((installed / "resources/app.asar").read_bytes(), loader)
        self.assertTrue(installation.intact(installed, fixture.state, allow_mod=True))
        self.assertEqual(target.read_bytes(), b"synthetic mod self-update v2")

    def test_publication_boundary_matrix(self):
        for upgrade in (False, True):
            with self.fixture(upgrade) as (fixture, *_):
                events = []
                with boundaries(
                    fixture, lambda label, phase, events=events: events.append((label, phase))
                ):
                    fixture.publish()
            # Test only the first staged copy; all subsequent copies share this boundary.
            events = [
                event
                for event in events
                if not event[0].startswith("incoming:copy:") or event[0] == "incoming:copy:1"
            ]
            for label, phase in events:
                for mode in ("crash", "error"):
                    if mode == "error" and phase == "after":
                        continue
                    with self.subTest(upgrade=upgrade, label=label, phase=phase, mode=mode):
                        with self.fixture(upgrade) as values:
                            fixture = values[0]
                            fired = False

                            def interrupt(actual, position, label=label, phase=phase, mode=mode):
                                nonlocal fired
                                if not fired and (actual, position) == (label, phase):
                                    fired = True
                                    if mode == "crash":
                                        os._exit(90)
                                    raise OSError(errno.ENOSPC, "injected disposable fixture fault")

                            def run(fixture=fixture, interrupt=interrupt):
                                with boundaries(fixture, interrupt):
                                    fixture.publish()

                            if mode == "crash":
                                self.crash(run)
                            else:
                                with self.assertRaises(OSError):
                                    run()
                                self.assertTrue(fired)
                            self.recover(fixture)
                            self.verify(*values)
                    print(f"COVERAGE publish upgrade={upgrade} {label} {phase} {mode}", flush=True)

    def interrupt_publication(self, fixture):
        def callback(label, phase):
            if (label, phase) == ("entry:replace:1", "after"):
                os._exit(90)

        def action():
            with boundaries(fixture, callback):
                fixture.publish()

        self.crash(action)

    def test_recovery_boundary_matrix(self):
        for upgrade in (False, True):
            with self.fixture(upgrade) as (fixture, *_):
                self.interrupt_publication(fixture)
                events = []
                with boundaries(
                    fixture, lambda label, phase, events=events: events.append((label, phase))
                ):
                    self.recover(fixture)
            for label, phase in events:
                with self.subTest(upgrade=upgrade, label=label, phase=phase):
                    with self.fixture(upgrade, "Vencord") as values:
                        fixture = values[0]
                        self.interrupt_publication(fixture)

                        def callback(actual, position, label=label, phase=phase):
                            if (actual, position) == (label, phase):
                                os._exit(90)

                        def action(fixture=fixture, callback=callback):
                            with boundaries(fixture, callback):
                                self.recover(fixture)

                        self.crash(action)
                        self.recover(fixture)
                        self.recover(fixture)  # Recovery must also be idempotent.
                        self.verify(*values)
                print(f"COVERAGE recovery upgrade={upgrade} {label} {phase}", flush=True)

    def test_first_takeover_crashes(self):
        for layout in ("stock", "legacy-symlink", "empty"):
            for label, phase in (
                ("incoming:rename:1", "after"),
                ("entry:replace:1", "after"),
                ("COMMIT:1", "before"),
                ("COMMIT:1", "after"),
            ):
                with self.subTest(layout=layout, label=label, phase=phase):
                    fixture = fixtures.NativeInstallation()
                    fixture.setUp()
                    try:
                        public = fixture.profile / "app-1.0.9"
                        entry = fixture.profile / "DiscordCanary"
                        if layout == "stock":
                            fixture.stock()
                            resources = public / "resources"
                            resources.mkdir()
                            (resources / "app.asar").write_text("stock bootstrap")
                        else:
                            view = installation.prepare_view(
                                fixture.app,
                                fixture.profile,
                                fixture.host,
                                fixture.exe,
                                fixture.state,
                            )
                            if layout == "legacy-symlink":
                                public.symlink_to(view)
                                entry.symlink_to(public / "DiscordCanary")
                        loader = None
                        if layout != "empty":
                            loader = install(public / "resources", "Vencord")
                        before = fixture.database() if layout == "stock" else None

                        def interrupt(actual, position, label=label, phase=phase):
                            if (actual, position) == (label, phase):
                                os._exit(90)

                        def action(fixture=fixture, interrupt=interrupt):
                            with boundaries(fixture, interrupt):
                                fixture.publish()

                        self.crash(action)
                        self.recover(fixture)
                        committed = label == "COMMIT:1" and phase == "after"
                        if not committed:
                            if layout == "stock":
                                self.assertEqual(fixture.database(), before)
                                self.assertEqual(entry.read_text(), "stock runtime")
                            elif layout == "legacy-symlink":
                                self.assertTrue(public.is_symlink())
                            else:
                                self.assertFalse(public.exists())
                                self.assertFalse(entry.is_symlink())
                        if loader:
                            self.assertEqual((public / "resources/app.asar").read_bytes(), loader)
                        fixture.publish()
                        self.assertFalse(public.is_symlink())
                        self.assertTrue(installation.intact(public, fixture.state, allow_mod=True))
                        if loader:
                            self.assertEqual((public / "resources/app.asar").read_bytes(), loader)
                        print(f"COVERAGE takeover {layout} {label} {phase}", flush=True)
                    finally:
                        fixture.doCleanups()


if __name__ == "__main__":
    unittest.main(verbosity=2)
