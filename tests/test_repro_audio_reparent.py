"""Regression: reparenting preserves the selected application audio.

Replay the video-link/cast fixtures in test_wayland_audio through Controller.tick,
plus its playback-node fixture through the actual Route.sync. All OS identities,
commands, compositor replies and child operations are synthetic/mocked.
"""

import contextlib
import copy
import io
import json
import unittest
from unittest.mock import Mock, patch

import test_wayland_audio as fixtures

audio = fixtures.audio


def existing_video_fixture():
    # Capture the exact existing fixture, without editing its source or allowing
    # any live graph query. Its test directly supplies the synthetic graph.
    with patch.object(audio, "video_sources", wraps=audio.video_sources) as observed:
        case = fixtures.WaylandIdentity()
        case.test_portal_broker_pid_is_not_confused_with_native_consumer()
        objects = copy.deepcopy(observed.call_args_list[0].args[0])
    # Existing fixture uses 200 for Discord; reserve that PID for the selected app.
    audio.props(objects[0])["application.process.id"] = 100
    with patch.object(audio, "niri_target", wraps=audio.niri_target) as observed:
        case.test_only_exact_cast_window_relation_can_select_audio()
        _sources, casts, windows = copy.deepcopy(observed.call_args_list[0].args)
    return objects, casts, windows


class AudioReparentRegression(unittest.TestCase):
    def test_reparented_application_keeps_audio_until_share_ends(self):
        objects, casts, windows = existing_video_fixture()
        # Same playback fixture shape and two stream IDs as the existing
        # test_application_includes_all_its_streams_and_excludes_other_apps.
        objects += [
            {
                "id": node_id,
                "type": "PipeWire:Interface:Node",
                "info": {
                    "props": {
                        "media.class": "Stream/Output/Audio",
                        "object.serial": str(node_id),
                        "application.process.id": pid,
                    }
                },
            }
            for node_id, pid in ((40, 200), (41, 201), (42, 300), (43, 100), (44, 901))
        ]
        identities = {
            100: (100, "discord-start", 1),
            200: (200, "application-start", 500),
            201: (201, "audio-child-start", 200),
            300: (300, "other-app-start", 1),
            500: (500, "launcher-start", 1),
            900: (900, "helper-start", 100),
            901: (901, "feed-start", 900),
        }
        children = []
        routes = []

        def start(route):
            routes.append(route)
            route.mix = "private-fixture-mix"
            route.capture_pid = 900
            return route.capture_pid

        def spawn(route, args, **_kwargs):
            child = Mock()
            child.poll.return_value = None
            child.args = args
            children.append(child)
            route.children.append(child)
            return child

        def request(name):
            return {"Casts": casts, "Windows": windows}[name]

        with (
            patch.dict(audio.os.environ, {"NIRI_SOCKET": "synthetic-never-opened"}, clear=True),
            patch.object(audio.os, "getpid", return_value=900),
            patch.object(audio, "process_identity", side_effect=identities.get),
            patch.object(audio, "discord_root", return_value=identities[100]),
            patch.object(audio, "graph", return_value=objects),
            patch.object(audio, "niri_request", side_effect=request),
            patch.object(audio, "command", return_value="[]"),
            patch.object(audio.sp, "Popen", side_effect=AssertionError("no real subprocess")),
            patch.object(audio.Route, "start", start),
            patch.object(audio.Route, "spawn", spawn),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            controller = audio.Controller(100)
            self.addCleanup(controller.close)
            controller.update({"generation": 1, "enabled": True, "hint": 0})
            controller.tick()
            self.assertEqual(controller.last["state"], "ready")
            self.assertEqual(set(controller.route.feeds), {("pw", "40"), ("pw", "41")})
            self.assertEqual(len(children), 2)  # unrelated, Discord and helper excluded
            before = identities[200]
            identities[200] = (200, "application-start", 1)
            del identities[500]  # launcher exits; selected application remains alive
            self.assertEqual(before[:2], identities[200][:2])
            for _ in range(3):
                controller.tick()
                self.assertEqual(controller.last["state"], "ready")
                self.assertEqual(set(controller.route.feeds), {("pw", "40"), ("pw", "41")})
            self.assertEqual(len(routes), 1)
            self.assertFalse(any(child.terminate.called for child in children))
            # Positive control: a new generation revives exactly the same app.
            controller.update({"generation": 2, "enabled": True, "hint": 0})
            controller.tick()
            self.assertEqual(controller.last["state"], "ready")
            self.assertEqual(set(controller.route.feeds), {("pw", "40"), ("pw", "41")})
            controller.close()
            self.assertTrue(all(child.wait.called for child in children))
            events = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertFalse(any(event["state"] == "unavailable" for event in events))
