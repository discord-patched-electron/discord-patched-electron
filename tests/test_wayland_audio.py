import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    "pipewire_audio",
    Path(__file__).resolve().parents[1] / "integration/audio/pipewire_audio.py",
)
audio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audio)


class WaylandIdentity(unittest.TestCase):
    def test_audio_picker_identity_preserves_selection_and_cancellation(self):
        for identity, root, reply, status, kind in (
            ("discord-canary", "/portable app", b"200\n", 0, "application"),
            ("discord-ptb", "", b"system\n", 0, "system"),
            ("discord", "/app", b"200\n", 1, None),
        ):
            with (
                self.subTest(identity=identity, kind=kind),
                patch.dict(
                    audio.os.environ,
                    {"CHROME_DESKTOP": identity + ".desktop", "DPE_APP_ROOT": root},
                ),
                patch.dict(
                    "sys.modules",
                    {
                        "window_inventory": SimpleNamespace(
                            windows=lambda: [], desktop_note=lambda: ""
                        )
                    },
                ),
                patch.object(audio, "process_identity", side_effect=lambda pid: (pid, "start", 1)),
                patch.object(audio, "discord_root", return_value=(100, "start", 1)),
                patch.object(audio, "descendant", return_value=False),
                patch.object(audio.sp, "Popen") as launch,
            ):
                child = Mock()
                child.poll.return_value = None
                launch.return_value = child
                controller = audio.Controller(100)
                pulse = [
                    {
                        "properties": {
                            "application.process.id": "200",
                            "application.name": "Test app",
                        }
                    }
                ]
                self.assertIsNone(controller.choose([], pulse, (20, "300")))
                args = launch.call_args.args[0]
                self.assertEqual(args[0], identity)
                self.assertEqual(launch.call_args.kwargs["executable"], "/usr/bin/zenity")
                self.assertIn(
                    "--window-icon=" + (root + "/discord.png" if root else identity), args
                )
                self.assertIn("--title=Screen-share audio", args)
                self.assertEqual(
                    args[args.index("--") + 1 :],
                    [
                        "system",
                        "Entire system (except Discord)",
                        "200",
                        "Test app — Application audio",
                    ],
                )
                child.poll.return_value = status
                child.returncode = status
                child.communicate.return_value = (reply, None)
                choice = controller.choose([], pulse, (20, "300"))
                if kind is None:
                    self.assertIsNone(choice)
                else:
                    self.assertEqual(choice["kind"], kind)
                    self.assertEqual(choice["backend"], "selection")
                    if kind == "application":
                        self.assertEqual(choice["pid"], 200)
                self.assertIsNone(controller.chooser)
                self.assertEqual(controller.choose([], pulse, (20, "300")), choice)
                launch.assert_called_once()

    def test_application_includes_all_its_streams_and_excludes_other_apps(self):
        nodes = [
            {
                "id": i,
                "type": "PipeWire:Interface:Node",
                "info": {
                    "props": {
                        "media.class": "Stream/Output/Audio",
                        "object.serial": str(i),
                        "application.process.id": 200,
                    }
                },
            }
            for i in (20, 21)
        ]
        route = audio.Route()
        route.mix = "private"
        other = {
            "id": 22,
            "type": "PipeWire:Interface:Node",
            "info": {
                "props": {
                    "media.class": "Stream/Output/Audio",
                    "object.serial": "22",
                    "application.process.id": 300,
                }
            },
        }
        with (
            patch.object(audio, "process_identity", side_effect=lambda pid: (pid, "start", 1)),
            patch.object(audio, "descendant", side_effect=lambda pid, root: pid == root[0]),
            patch.object(route, "spawn") as spawn,
        ):
            route.sync([*nodes, other], [], (200, "start", 1), (100, "discord", 1))
            self.assertEqual(list(route.feeds), [("pw", "20"), ("pw", "21")])
            self.assertEqual(spawn.call_count, 2)

    def test_pulse_app_exit_or_pid_reuse_during_sink_lookup_stays_silent(self):
        for replacement in (None, (200, "new-start-time", 1)):
            with self.subTest(replacement=replacement):
                route = audio.Route()
                original = (200, "original-start-time", 1)
                current = original

                def sinks(_args, replacement=replacement):
                    nonlocal current
                    current = replacement
                    return '[{"index":4,"monitor_source_name":"test.monitor"}]'

                with (
                    patch.object(
                        audio,
                        "process_identity",
                        side_effect=lambda pid: current if pid == 200 else (pid, "self", 1),  # noqa: B023 - reads this subtest's mutable identity
                    ),
                    patch.object(audio, "descendant", return_value=False),
                    patch.object(audio, "command", side_effect=sinks),
                    patch.object(route, "spawn") as spawn,
                ):
                    route.sync(
                        [],
                        [{"index": 3, "sink": 4, "properties": {"application.process.id": "200"}}],
                        None,
                        (100, "discord", 1),
                    )
                    spawn.assert_not_called()
                    self.assertEqual(route.feeds, {})

    def test_portal_broker_pid_is_not_confused_with_native_consumer(self):
        objects = [
            {
                "id": 10,
                "type": "PipeWire:Interface:Client",
                "info": {"props": {"application.process.id": 200, "pipewire.sec.pid": 99}},
            },
            {
                "id": 11,
                "type": "PipeWire:Interface:Node",
                "info": {"props": {"client.id": 10, "media.class": "Stream/Input/Video"}},
            },
            {
                "id": 20,
                "type": "PipeWire:Interface:Node",
                "info": {"props": {"object.serial": 300}},
            },
            {
                "id": 30,
                "type": "PipeWire:Interface:Link",
                "info": {"input-node-id": 11, "output-node-id": 20, "state": "active"},
            },
        ]
        self.assertEqual(audio.video_sources(objects, 200), {(20, "300")})
        self.assertEqual(audio.video_sources(objects, 99), set())

    def test_only_exact_cast_window_relation_can_select_audio(self):
        cast = {
            "pw_node_id": 20,
            "session_id": 1,
            "stream_id": 2,
            "is_active": True,
            "target": {"Window": {"id": 7}},
        }
        unrelated = {**cast, "pw_node_id": 21, "target": {"Window": {"id": 8}}}
        windows = [{"id": 7, "pid": 200}, {"id": 8, "pid": 300, "is_focused": True}]
        target = audio.niri_target({(20, "400")}, [unrelated, cast], windows)
        self.assertEqual(target["pid"], 200)
        self.assertIsNone(audio.niri_target({(20, "400")}, [unrelated], windows))
        self.assertIsNone(audio.niri_target({(20, "400"), (21, "401")}, [cast, unrelated], windows))
        self.assertIsNone(audio.niri_target({(20, "400")}, [cast, cast], windows))
        self.assertIsNone(audio.niri_target({(20, "400")}, [{**cast, "is_active": False}], windows))
        self.assertIsNone(audio.niri_target({(20, "400")}, [cast], [{"id": 7, "pid": None}]))

    def test_whole_monitor_is_distinguished_from_an_opaque_screen_token(self):
        cast = {
            "pw_node_id": 20,
            "session_id": 1,
            "stream_id": 2,
            "is_active": True,
            "target": {"Output": {"name": "test"}},
        }
        self.assertEqual(audio.niri_target({(20, "400")}, [cast], [])["kind"], "system")
        self.assertIsNone(
            audio.niri_target({(20, "400")}, [{**cast, "target": {"Nothing": {}}}], [])
        )


class PlaybackDiscovery(unittest.TestCase):
    def node(self, pid=None):
        properties = {"media.class": "Stream/Output/Audio", "object.serial": "20", "client.id": 10}
        if pid is not None:
            properties["application.process.id"] = pid
        return {"id": 20, "type": "PipeWire:Interface:Node", "info": {"props": properties}}

    def test_pulse_metadata_fills_missing_pipewire_pid_and_is_mixed_once(self):
        pulse = [
            {
                "properties": {
                    "object.serial": "20",
                    "application.process.id": "200",
                    "application.name": "Spotify",
                }
            }
        ]
        entries = list(audio.playback_entries([self.node()], pulse))
        self.assertEqual(entries[0][1]["application.name"], "Spotify")
        self.assertEqual(audio.audio_pid(entries[0][1]), 200)
        route = audio.Route()
        route.mix = "private"
        with (
            patch.object(audio, "process_identity", side_effect=lambda pid: (pid, "start", 1)),
            patch.object(audio, "descendant", side_effect=lambda pid, root: pid == root[0]),
            patch.object(route, "spawn") as spawn,
        ):
            route.sync([self.node()], pulse, (200, "start", 1), (100, "discord", 1))
        self.assertEqual(list(route.feeds), [("pw", "20")])
        spawn.assert_called_once()

    def test_flatpak_instance_overrides_namespace_pid_and_server_pid(self):
        properties = {
            "application.process.id": "2",
            "pipewire.sec.pid": 999,
            "pipewire.access.portal.app_id": "com.spotify.Client",
            "pipewire.access.portal.instance_id": "42",
        }
        metadata = "[Application]\nname=com.spotify.Client\n[Instance]\ninstance-id=42\n"
        for content, expected in (
            (metadata, 200),
            (metadata.replace("42", "43"), -1),
            (metadata.replace("com.spotify.Client", "other.app"), -1),
        ):
            with (
                patch.object(audio.Path, "read_text", side_effect=['{"child-pid":200}', content]),
                patch.object(audio, "process_identity", return_value=(200, "start", 1)),
            ):
                self.assertEqual(audio.audio_pid(properties), expected)
        with (
            patch.object(audio.Path, "read_text", side_effect=FileNotFoundError),
            patch.object(audio, "process_identity", return_value=(2, "unrelated-host-process", 1)),
        ):
            self.assertEqual(audio.audio_pid(properties), -1)
        for instance in ("../42", "", "٤٢"):
            self.assertEqual(
                audio.audio_pid({**properties, "pipewire.access.portal.instance_id": instance}), -1
            )
        self.assertEqual(
            audio.audio_pid(
                {
                    "pipewire.access.portal.app_id": "com.spotify.Client",
                    "application.process.id": "2",
                }
            ),
            -1,
        )

    def test_reused_flatpak_root_is_rejected(self):
        with (
            patch.object(
                audio.Path,
                "read_text",
                side_effect=[
                    '{"child-pid":200}',
                    "[Application]\nname=app\n[Instance]\ninstance-id=42\n",
                ],
            ),
            patch.object(audio, "process_identity", side_effect=[(200, "old", 1), (200, "new", 1)]),
        ):
            self.assertEqual(
                audio.audio_pid(
                    {
                        "pipewire.access.portal.app_id": "app",
                        "pipewire.access.portal.instance_id": "42",
                    }
                ),
                -1,
            )

    def test_client_sandbox_identity_cannot_be_overridden_by_stream_properties(self):
        client = {
            "id": 10,
            "type": "PipeWire:Interface:Client",
            "info": {
                "props": {
                    "pipewire.access.portal.instance_id": "42",
                    "pipewire.access.portal.app_id": "com.spotify.Client",
                }
            },
        }
        pulse = [
            {"properties": {"object.serial": "20", "pipewire.access.portal.instance_id": "99"}}
        ]
        properties = list(audio.playback_entries([client, self.node()], pulse))[0][1]
        self.assertEqual(properties["pipewire.access.portal.instance_id"], "42")

    def test_picker_works_without_window_metadata_and_can_refresh(self):
        import sys
        from unittest.mock import Mock

        inventory = Mock()
        inventory.windows.return_value = []
        inventory.desktop_note.return_value = "Audio selection remains manual."
        child = Mock()
        child.poll.return_value = None
        with (
            patch.dict(sys.modules, {"window_inventory": inventory}),
            patch.object(audio, "process_identity", side_effect=lambda pid: (pid, "start", 1)),
            patch.object(audio, "discord_root", return_value=(100, "start", 1)),
            patch.object(audio, "descendant", side_effect=lambda pid, root: pid == root[0]),
            patch.object(audio.sp, "Popen", return_value=child) as launch,
        ):
            controller = audio.Controller(100)
            self.assertIsNone(controller.choose([self.node(200), self.node(100)], [], (30, "40")))
            args = launch.call_args.args[0]
            self.assertIn("200", args)
            self.assertNotIn("100", args)
            self.assertIn("--no-markup", args)
            child.poll.return_value = 1
            child.communicate.return_value = (b"Refresh applications\n", b"")
            child.returncode = 1
            self.assertIsNone(controller.choose([], [], (30, "40")))
            child.poll.return_value = None
            controller.choose([self.node(300)], [], (30, "40"))
            self.assertEqual(launch.call_count, 2)
            self.assertIn("300", launch.call_args.args[0])
            self.assertNotIn("200", launch.call_args.args[0])

    def test_inline_picker_metadata_refresh_stale_replies_and_reused_pids(self):
        import io
        import json
        import sys
        from contextlib import redirect_stdout
        from unittest.mock import Mock

        inventory = Mock()
        inventory.windows.return_value = []
        identities = {100: (100, "a", 1), 200: (200, "b", 1)}
        with (
            patch.dict(sys.modules, {"window_inventory": inventory}),
            patch.object(audio, "process_identity", side_effect=identities.get),
            patch.object(audio, "discord_root", return_value=identities[100]),
            patch.object(
                audio, "descendant", side_effect=lambda pid, root: root and pid == root[0]
            ),
            patch.object(audio.sp, "Popen") as launch,
            redirect_stdout(io.StringIO()) as output,
        ):
            controller = audio.Controller(100)
            controller.update({"generation": 2, "enabled": True, "picker": "inline"})
            node = self.node(200)
            node["info"]["props"].update(
                {"application.id": "org.example.Player", "media.name": "Music <test>"}
            )
            controller.choose([node], [], (30, "40"))
            request = json.loads(output.getvalue().splitlines()[-1])
            self.assertEqual(
                request["options"][1],
                {
                    "id": "200",
                    "label": "org.example.Player",
                    "detail": "Music <test>",
                    "iconNames": ["org.example.Player"],
                },
            )
            launch.assert_not_called()
            controller.update({"generation": 1, "request": 1, "selection": "system"})
            self.assertTrue(controller.pending_choice)
            controller.update({"generation": 2, "request": 1, "selection": "refresh"})
            controller.choose([node], [], (30, "40"))
            controller.update({"generation": 2, "request": 1, "selection": "system"})
            self.assertTrue(controller.pending_choice)
            identities[200] = (200, "reused", 1)
            controller.update({"generation": 2, "request": 2, "selection": "200"})
            self.assertIsNone(controller.choice)
            controller.reset()
            self.assertEqual(json.loads(output.getvalue().splitlines()[-1])["state"], "waiting")
            controller.choose([node], [], (30, "40"))
            controller.update({"generation": 2, "request": 3, "selection": "none"})
            self.assertIsNone(controller.choose([node], [], (30, "40")))
            self.assertFalse(controller.pending_choice)
            self.assertEqual(controller.request, 3)
            controller.reset()
            controller.choose([node], [], (30, "40"))
            controller.update({"generation": 2, "request": 4, "selection": "200"})
            self.assertEqual(controller.choice["pid"], 200)

    def test_flatpak_selection_routes_only_that_instance_and_excludes_discord(self):
        objects = []
        for client_id, instance, pid in ((10, "42", 200), (11, "43", 300), (12, "44", 100)):
            objects.append(
                {
                    "id": client_id,
                    "type": "PipeWire:Interface:Client",
                    "info": {
                        "props": {
                            "application.process.id": "2",
                            "pipewire.access.portal.instance_id": instance,
                            "pipewire.access.portal.app_id": "app",
                        }
                    },
                }
            )
            node = self.node()
            node["info"]["props"].update({"client.id": client_id, "object.serial": str(pid)})
            objects.append(node)

        def read(path):
            if path.name == "bwrapinfo.json":
                return (
                    '{"child-pid":' + str({"42": 200, "43": 300, "44": 100}[path.parent.name]) + "}"
                )
            instance = {200: "42", 300: "43", 100: "44"}[int(path.parts[2])]
            return f"[Application]\nname=app\n[Instance]\ninstance-id={instance}\n"

        route = audio.Route()
        route.mix = "private"
        with (
            patch.object(audio.Path, "read_text", autospec=True, side_effect=read),
            patch.object(audio, "process_identity", side_effect=lambda pid: (pid, "start", 1)),
            patch.object(
                audio,
                "descendant",
                side_effect=lambda pid, root: (
                    pid == root[0] or (pid, root[0]) in {(201, 200), (101, 100)}
                ),
            ),
            patch.object(route, "spawn") as spawn,
        ):
            spawn.return_value.poll.return_value = None
            route.sync(objects, [], (200, "start", 1), (100, "start", 1))
            self.assertEqual(list(route.feeds), [("pw", "200")])
            # An automatic window PID inside the selected sandbox still matches
            # its application; Discord inside another sandbox remains excluded.
            route.sync(objects, [], (201, "start", 200), (101, "start", 100))
            self.assertEqual(list(route.feeds), [("pw", "200")])
            route.sync(objects, [], None, (101, "start", 100))
            self.assertEqual(set(route.feeds), {("pw", "200"), ("pw", "300")})

    def test_video_disappearing_closes_the_pending_audio_picker(self):
        from unittest.mock import Mock

        with (
            patch.object(audio, "process_identity", return_value=(100, "start", 1)),
            patch.object(audio, "discord_root", return_value=(100, "start", 1)),
            patch.object(audio, "graph", return_value=[]),
            patch.object(audio, "command", return_value="[]"),
            patch.object(audio.Controller, "emit"),
            patch.object(audio.Route, "stop") as stop,
        ):
            controller = audio.Controller(100)
            controller.enabled = True
            chooser = controller.chooser = Mock()
            controller.tick()
            stop.assert_called_once_with(chooser)
            self.assertIsNone(controller.chooser)
