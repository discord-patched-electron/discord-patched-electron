import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "pipewire_audio",
    Path(__file__).resolve().parents[1] / "integration/audio/pipewire_audio.py",
)
audio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audio)


class WaylandIdentity(unittest.TestCase):
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

                def sinks(_args):
                    nonlocal current
                    current = replacement
                    return '[{"index":4,"monitor_source_name":"test.monitor"}]'

                with (
                    patch.object(
                        audio,
                        "process_identity",
                        side_effect=lambda pid: current if pid == 200 else (pid, "self", 1),
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
