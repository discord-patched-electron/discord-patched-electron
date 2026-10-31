import contextlib
import importlib
import io
import json
import unittest
from unittest.mock import Mock, patch

from test_wayland_audio import audio

detection = importlib.import_module("capture_detection")


def identity(pid):
    return (pid, "start", 1) if type(pid) is int and pid > 1 else None


def descendant(pid, root):
    return pid == root[0] or (pid, root[0]) == (201, 200)


class CaptureDetection(unittest.TestCase):
    def setUp(self):
        self.objects = [
            {
                "id": 42,
                "type": "PipeWire:Interface:Node",
                "info": {"props": {"object.serial": "100"}},
            }
        ]
        self.observation = {
            "node": 42,
            "serial": "100",
            "token": 1,
            "kind": "application",
            "appId": "org.mozilla.firefox.desktop",
            "backend": "portal-gnome",
        }
        self.windows = [
            {
                "pid": 200,
                "app_id": "org.mozilla.firefox",
                "backend": "gnome-window-calls",
                "id": 999,
            }
        ]

    def target(self, observations=None, windows=None, entries=None):
        return detection.target(
            self.objects,
            (42, "100"),
            observations if observations is not None else [self.observation],
            windows if windows is not None else self.windows,
            entries or [],
            identity,
            descendant,
            audio.audio_pid,
        )

    def test_apps_monitors_and_exact_mutter_windows(self):
        self.assertEqual(self.target()["pid"], 200)
        self.assertEqual(self.target([{**self.observation, "kind": "system"}])["kind"], "system")
        self.assertEqual(
            self.target([{**self.observation, "backend": "mutter-window", "windowId": 999}])["pid"],
            200,
        )
        self.assertIsNone(
            self.target(
                [{**self.observation, "backend": "mutter-window", "windowId": 998, "appId": None}]
            )
        )

    def test_multiple_process_families_missing_pids_and_other_streams_stay_manual(self):
        for other in [300, None, True]:
            self.assertIsNone(
                self.target(
                    windows=self.windows + [{"pid": other, "app_id": "org.mozilla.firefox"}]
                )
            )
        for change in [{"node": 43}, {"serial": "101"}, {"appId": "other"}]:
            self.assertIsNone(self.target([{**self.observation, **change}]))
        self.assertIsNone(self.target([self.observation, self.observation]))
        self.assertEqual(
            self.target(windows=self.windows + [{"pid": 201, "app_id": "org.mozilla.firefox"}])[
                "pid"
            ],
            200,
        )

    def test_audio_metadata_matches_exact_ids_but_not_names_or_titles(self):
        entry = {"application.id": "org.mozilla.firefox", "application.process.id": "200"}
        self.assertEqual(self.target(windows=[], entries=[entry])["pid"], 200)
        self.assertIsNone(
            self.target(
                windows=[],
                entries=[
                    {"application.name": "org.mozilla.firefox", "application.process.id": "200"}
                ],
            )
        )
        self.assertIsNone(
            self.target(windows=[], entries=[entry, {**entry, "application.process.id": "300"}])
        )
        self.assertIsNone(
            self.target(
                windows=[],
                entries=[{**entry, "pipewire.access.portal.app_id": "org.mozilla.firefox"}],
            )
        )

    def test_kwin_stream_fallback_requires_live_kwin_app_id_not_output_name(self):
        self.objects[0]["info"]["props"]["media.name"] = "kwin-screencast-org.mozilla.firefox"
        self.assertIsNone(self.target([]))
        self.assertEqual(self.target([], [{**self.windows[0], "backend": "kwin"}])["pid"], 200)
        self.objects[0]["info"]["props"]["media.name"] = "kwin-screencast-DP-1"
        self.assertIsNone(self.target([], [{**self.windows[0], "backend": "kwin"}]))

    def controller(self):
        return audio.Controller(100)

    def test_off_switch_bypasses_niri_hint_and_portal_but_preserves_manual_picker(self):
        with (
            patch.object(audio, "process_identity", side_effect=identity),
            patch.object(audio, "discord_root", return_value=identity(100)),
            patch.object(audio, "graph", return_value=self.objects),
            patch.object(audio, "command", return_value="[]"),
            patch.object(audio, "video_sources", return_value={(42, "100")}),
            patch.dict(audio.os.environ, {"NIRI_SOCKET": "fixture"}),
            patch.object(audio, "niri_request") as niri,
        ):
            controller = self.controller()
            controller.update(
                {
                    "generation": 1,
                    "enabled": True,
                    "autoDetect": False,
                    "hint": 200,
                    "observations": [self.observation],
                }
            )
            controller.choose = Mock(return_value=None)
            controller.detect_portal = Mock()
            controller.tick()
            controller.choose.assert_called_once()
            controller.detect_portal.assert_not_called()
            niri.assert_not_called()
            self.assertEqual(controller.observations, [])

    def test_forced_reenable_bypasses_every_detection_backend_until_next_source(self):
        with (
            patch.object(audio, "process_identity", side_effect=identity),
            patch.object(audio, "discord_root", return_value=identity(100)),
            patch.object(audio, "descendant", side_effect=descendant),
            patch.object(audio, "graph", return_value=self.objects),
            patch.object(audio, "command", return_value="[]"),
            patch.object(audio, "video_sources", return_value={(42, "100")}),
            patch.object(audio.time, "monotonic", return_value=10),
            patch.dict(audio.os.environ, {"NIRI_SOCKET": "fixture"}),
            patch.object(audio, "niri_request") as niri,
            patch.object(audio, "Route") as route,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            controller = self.controller()
            controller.choose = Mock(return_value=None)
            controller.detect_portal = Mock()
            controller.update(
                {
                    "generation": 1,
                    "enabled": True,
                    "autoDetect": True,
                    "forcePicker": True,
                    "hint": 200,
                    "observations": [self.observation],
                }
            )
            controller.tick()
            controller.choose.assert_called_once()
            controller.detect_portal.assert_not_called()
            niri.assert_not_called()
            route.assert_not_called()
            self.assertEqual(controller.hint, 200)
            self.assertEqual(controller.observations, [])
            self.assertEqual(controller.detection_deadline, 0)
            controller.update({"generation": 1, "observations": [self.observation]})
            controller.tick()
            self.assertEqual(controller.observations, [])
            route.assert_not_called()
            # The next video selection uses the saved preference, not the prior
            # generation's request to make an explicit audio choice.
            route.return_value.capture_pid = 900
            with patch.dict(audio.os.environ, {}, clear=True):
                controller.update(
                    {
                        "generation": 2,
                        "enabled": True,
                        "autoDetect": True,
                        "forcePicker": False,
                        "hint": 200,
                    }
                )
                controller.tick()
            route.assert_called_once()
            self.assertEqual(controller.last["state"], "ready")
            self.assertEqual(controller.last["backend"], "native-hint")

    def test_forced_chooser_emits_options_and_respects_a_different_app_than_hint(self):
        pulse = [
            {
                "corked": False,
                "mute": False,
                "properties": {"application.process.id": str(pid), "application.name": label},
            }
            for pid, label in [(200, "Shared window"), (300, "Music player")]
        ]
        with (
            patch.object(audio, "process_identity", side_effect=identity),
            patch.object(audio, "discord_root", return_value=identity(100)),
            patch.object(audio, "descendant", side_effect=descendant),
            patch.object(audio, "graph", return_value=self.objects),
            patch.object(audio, "command", return_value=json.dumps(pulse)),
            patch.object(audio, "video_sources", return_value={(42, "100")}),
            patch("window_inventory.windows", return_value=[]),
            patch.object(audio, "Route") as route,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            controller = self.controller()
            controller.update(
                {
                    "generation": 1,
                    "enabled": True,
                    "autoDetect": True,
                    "forcePicker": True,
                    "hint": 200,
                    "picker": "inline",
                }
            )
            controller.tick()
            choosing = json.loads(output.getvalue().splitlines()[-1])
            self.assertEqual(choosing["state"], "choosing")
            self.assertEqual([o["id"] for o in choosing["options"]], ["system", "200", "300"])
            route.assert_not_called()
            controller.update({"generation": 1, "request": choosing["request"], "selection": "300"})
            route.return_value.capture_pid = 900
            controller.tick()
            self.assertEqual(controller.target, identity(300))
            self.assertEqual(controller.last["backend"], "selection")
            self.assertEqual(controller.last["state"], "ready")
            controller.tick()
            route.assert_called_once()
            self.assertEqual(controller.target, identity(300))

    def test_observations_bind_to_serial_and_never_override_open_manual_picker(self):
        with (
            patch.object(audio, "process_identity", side_effect=identity),
            patch.object(audio, "discord_root", return_value=identity(100)),
            patch.object(audio, "descendant", side_effect=descendant),
            patch("window_inventory.windows", return_value=self.windows),
        ):
            controller = self.controller()
            controller.set_observations([self.observation])
            target = controller.detect_portal(self.objects, [], (42, "100"))
            self.assertEqual(target["pid"], 200)
            controller.reset()
            reused = [{**self.objects[0], "info": {"props": {"object.serial": "101"}}}]
            self.assertIsNone(controller.detect_portal(reused, [], (42, "101")))
            controller.update({"generation": 1, "enabled": True})
            controller.choose_key = (42, "100")
            controller.pending_choice = True
            controller.detect_portal = Mock()
            with (
                patch.object(audio, "graph", return_value=self.objects),
                patch.object(audio, "command", return_value="[]"),
                patch.object(audio, "video_sources", return_value={(42, "100")}),
                patch.dict(audio.os.environ, {}, clear=True),
            ):
                controller.tick()
            controller.detect_portal.assert_not_called()

    def test_detected_process_exit_or_reuse_cannot_retarget_same_stream(self):
        processes = {100: (100, "owner", 1), 200: (200, "app", 1)}
        with (
            patch.object(audio, "process_identity", side_effect=processes.get),
            patch.object(audio, "discord_root", return_value=processes[100]),
            patch.object(audio, "descendant", side_effect=descendant),
            patch.object(audio, "graph", return_value=self.objects),
            patch.object(audio, "command", return_value="[]"),
            patch.object(audio, "video_sources", return_value={(42, "100")}),
            patch("window_inventory.windows", return_value=self.windows),
            patch.object(audio, "Route") as route,
            patch.dict(audio.os.environ, {}, clear=True),
        ):
            route.return_value.capture_pid = 900
            controller = self.controller()
            controller.update(
                {"generation": 1, "enabled": True, "observations": [self.observation]}
            )
            controller.tick()
            self.assertEqual(controller.last["state"], "ready")
            processes[200] = None
            controller.tick()
            self.assertEqual(controller.last["state"], "unavailable")
            processes[200] = (200, "replacement", 1)
            controller.tick()
            self.assertEqual(controller.last["state"], "unavailable")
            route.assert_called_once()
            route.return_value.close.assert_called_once()

    def test_late_observation_wait_is_bounded_and_cancellation_stays_manual(self):
        with (
            patch.object(audio, "process_identity", side_effect=identity),
            patch.object(audio, "discord_root", return_value=identity(100)),
            patch.object(audio, "graph", return_value=self.objects),
            patch.object(audio, "command", return_value="[]"),
            patch.object(audio, "video_sources", return_value={(42, "100")}),
            patch.object(audio.time, "monotonic", return_value=10) as clock,
            patch.dict(audio.os.environ, {}, clear=True),
        ):
            controller = self.controller()
            controller.update({"generation": 1, "enabled": True})
            controller.choose = Mock(return_value=None)
            controller.tick()
            controller.choose.assert_not_called()
            clock.return_value = 12
            controller.tick()
            controller.choose.assert_called_once()
            controller.choose_key = (42, "100")
            controller.update({"generation": 0, "observations": [self.observation]})
            self.assertEqual(controller.observations, [])
            controller.update({"generation": 1, "observations": [self.observation]})
            controller.detect_portal = Mock()
            controller.tick()
            controller.detect_portal.assert_not_called()

    def test_manual_picker_ranks_playing_media_then_native_hint_without_sharing(self):
        pulse = [
            {
                "corked": paused,
                "mute": False,
                "properties": {
                    "application.process.id": str(pid),
                    "application.name": label,
                    "media.role": role,
                },
            }
            for pid, label, role, paused in [
                (200, "C paused player", "Music", True),
                (300, "B video", "Video", False),
                (400, "A notification", "Notification", False),
                (100, "Discord", "Video", False),
            ]
        ]
        with (
            patch.object(audio, "process_identity", side_effect=identity),
            patch.object(audio, "discord_root", return_value=identity(100)),
            patch.object(audio, "descendant", side_effect=descendant),
            patch("window_inventory.windows", return_value=[]),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            controller = self.controller()
            controller.inline_picker = True
            controller.choose([], pulse, (42, "100"))
            options = json.loads(output.getvalue().splitlines()[-1])["options"]
            self.assertEqual([o["id"] for o in options], ["system", "300", "400", "200"])
            self.assertIsNone(controller.choice)
            self.assertIsNone(controller.route)
            controller.reset()
            controller.hint = 200
            controller.choose([], pulse, (42, "100"))
            options = json.loads(output.getvalue().splitlines()[-1])["options"]
            self.assertEqual(options[1]["id"], "200")
            self.assertIsNone(controller.choice)
