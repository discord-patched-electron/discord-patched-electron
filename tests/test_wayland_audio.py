from pathlib import Path
import importlib.util
import unittest

spec = importlib.util.spec_from_file_location(
    "pipewire_audio",
    Path(__file__).resolve().parents[1] / "integration/audio/pipewire_audio.py",
)
audio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audio)


class WaylandIdentity(unittest.TestCase):
    def test_portal_broker_pid_is_not_confused_with_native_consumer(self):
        objects = [
            {
                "id": 10,
                "type": "PipeWire:Interface:Client",
                "info": {
                    "props": {"application.process.id": 200, "pipewire.sec.pid": 99}
                },
            },
            {
                "id": 11,
                "type": "PipeWire:Interface:Node",
                "info": {
                    "props": {"client.id": 10, "media.class": "Stream/Input/Video"}
                },
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
        self.assertIsNone(
            audio.niri_target({(20, "400"), (21, "401")}, [cast, unrelated], windows)
        )
        self.assertIsNone(audio.niri_target({(20, "400")}, [cast, cast], windows))
        self.assertIsNone(
            audio.niri_target({(20, "400")}, [{**cast, "is_active": False}], windows)
        )
        self.assertIsNone(
            audio.niri_target({(20, "400")}, [cast], [{"id": 7, "pid": None}])
        )

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
