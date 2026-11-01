"""Exercise automatic confidence and manual consent through the real router.

Nuvio's libmpv stream can omit its PID while the PipeWire client supplies it.
niri can report xwayland-satellite's PID for the window instead of the app's.
All process, graph and compositor data here are synthetic; no audio is recorded.
"""

import contextlib
import io
import json
import unittest
from unittest.mock import Mock, patch

from test_wayland_audio import audio


def node(node_id, pid):
    return {
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


class AudioConfidence(unittest.TestCase):
    def setUp(self):
        self.processes = {
            100: (100, "discord", 1),
            200: (200, "nuvio", 1),
            201: (201, "app-child", 200),
            300: (300, "other-app", 1),
            400: (400, "xwayland-proxy", 1),
            900: (900, "helper", 100),
        }
        self.objects = [
            {
                "id": 60,
                "type": "PipeWire:Interface:Client",
                "info": {
                    "props": {
                        "application.process.id": 200,
                        "application.name": "Nuvio",
                        "application.process.binary": "Nuvio",
                    }
                },
            },
            {
                "id": 61,
                "type": "PipeWire:Interface:Node",
                "info": {
                    "props": {
                        "media.class": "Stream/Output/Audio",
                        "object.serial": "61",
                        "client.id": 60,
                        "application.name": "mpv",
                        "application.id": "mpv",
                    }
                },
            },
            node(62, 300),
            node(63, 100),
        ]
        self.pulse = [{"properties": {"object.serial": "61", "application.name": "mpv"}}]
        self.source = {(42, "100")}
        self.window_pid = 400
        self.window_class = None
        self.routes = []
        self.output = io.StringIO()
        stack = self.enterContext(contextlib.ExitStack())
        for manager in (
            patch.dict(audio.os.environ, {"NIRI_SOCKET": "fixture"}, clear=True),
            patch.object(audio.os, "getpid", return_value=900),
            patch.object(audio, "process_identity", side_effect=self.processes.get),
            patch.object(audio, "capture_proxy", side_effect=lambda pid: pid == 400),
            patch.object(audio, "discord_root", return_value=self.processes[100]),
            patch.object(audio, "graph", return_value=self.objects),
            patch.object(audio, "command", side_effect=lambda _args: json.dumps(self.pulse)),
            patch.object(audio, "video_sources", return_value=self.source),
            patch.object(audio, "niri_request", side_effect=self.niri),
            patch("window_inventory.windows", return_value=[]),
            patch("window_inventory.desktop_note", return_value=""),
            patch.object(audio.sp, "Popen", side_effect=AssertionError("no real subprocesses")),
            patch.object(audio.Route, "start", lambda route: self.start_route(route)),
            patch.object(audio.Route, "spawn", self.spawn),
            contextlib.redirect_stdout(self.output),
        ):
            stack.enter_context(manager)
        self.clock = stack.enter_context(patch.object(audio.time, "monotonic", return_value=10))
        self.controller = audio.Controller(100)
        self.addCleanup(self.controller.close)
        self.controller.update({"generation": 1, "enabled": True, "picker": "inline", "hint": 400})

    def niri(self, request):
        if request == "Windows":
            return [{"id": 7, "pid": self.window_pid, "app_id": self.window_class}]
        return [
            {
                "pw_node_id": 42,
                "session_id": 1,
                "stream_id": 2,
                "is_active": True,
                "target": {"Window": {"id": 7}},
            }
        ]

    def start_route(self, route):
        self.routes.append(route)
        route.mix = "fixture-private-mix"
        route.capture_pid = 900
        return 900

    @staticmethod
    def spawn(route, args, **_kwargs):
        child = Mock(args=args)
        child.poll.return_value = None
        route.children.append(child)
        return child

    def events(self, state):
        return [
            event
            for line in self.output.getvalue().splitlines()
            if (event := json.loads(line))["state"] == state
        ]

    def reply(self, selection):
        self.controller.update(
            {"generation": 1, "request": self.controller.request, "selection": selection}
        )
        self.controller.tick()

    def open_uncertain_picker(self):
        self.controller.tick()
        self.assertTrue(self.controller.pending_choice)
        self.assertIsNone(self.controller.route)
        self.assertEqual(len(self.events("choosing")), 1)
        self.assertEqual(self.events("ready"), [])

    def test_nuvio_proxy_opens_picker_and_routes_client_owned_mpv_once(self):
        self.open_uncertain_picker()
        options = self.events("choosing")[-1]["options"]
        self.assertEqual({option["id"] for option in options}, {"system", "200", "300"})
        self.assertEqual(next(o for o in options if o["id"] == "200")["label"], "mpv")
        self.reply("200")
        self.assertEqual(self.controller.last["backend"], "selection")
        self.assertEqual(set(self.controller.route.feeds), {("pw", "61")})
        for pid in (400, 300, 200):
            self.window_pid = pid  # Late niri results never replace explicit consent.
            self.controller.tick()
            self.assertEqual(self.controller.target[:2], self.processes[200][:2])
            self.assertEqual(set(self.controller.route.feeds), {("pw", "61")})

    def test_refresh_stays_manual_even_when_automatic_match_becomes_available(self):
        self.open_uncertain_picker()
        self.window_pid = 200
        before = len(self.output.getvalue())
        self.reply("refresh")
        refreshed = [json.loads(line) for line in self.output.getvalue()[before:].splitlines()]
        self.assertEqual([event["state"] for event in refreshed], ["choosing"])
        self.assertTrue(self.controller.pending_choice)
        self.assertEqual(len(self.events("choosing")), 2)
        self.assertEqual(self.events("ready"), [])
        self.reply("300")
        self.assertEqual(set(self.controller.route.feeds), {("pw", "62")})

    def test_dismissal_stays_silent_until_a_new_video_source(self):
        self.open_uncertain_picker()
        self.reply("")
        self.window_pid = 200
        for _ in range(3):
            self.controller.tick()
            self.assertIsNone(self.controller.route)
        self.assertEqual(len(self.events("choosing")), 1)
        self.assertEqual(self.events("ready"), [])
        self.source.clear()
        self.source.add((42, "101"))
        self.controller.tick()
        self.assertEqual(self.controller.last["backend"], "niri")
        self.assertEqual(set(self.controller.route.feeds), {("pw", "61")})

    def test_late_playback_with_proven_child_owner_does_not_open_picker(self):
        self.window_pid = 200
        self.objects[:] = [node(62, 300)]
        self.controller.tick()
        self.assertEqual(self.controller.last["state"], "waiting")
        for elapsed in (2, 30, 300):
            self.clock.return_value = 10 + elapsed
            self.controller.tick()
            self.assertEqual(self.events("choosing"), [])
            self.assertEqual(self.controller.route.feeds, {})
        self.objects.append(node(64, 201))
        self.controller.tick()
        self.assertEqual(self.controller.last["state"], "ready")
        self.assertEqual(set(self.controller.route.feeds), {("pw", "64")})
        # A confirmed app can pause and recreate its stream without reopening UI.
        self.objects.pop()
        self.clock.return_value = 600
        self.controller.tick()
        self.assertEqual(self.events("choosing"), [])
        self.objects.append(node(65, 201))
        self.controller.tick()
        self.assertEqual(set(self.controller.route.feeds), {("pw", "65")})

    def test_missing_window_pid_opens_picker_without_routing_another_app(self):
        self.window_pid = 999
        self.controller.tick()
        self.assertTrue(self.controller.pending_choice)
        self.assertEqual(self.events("ready"), [])
        self.assertEqual(self.routes, [])

    def test_xres_owner_waits_for_late_nuvio_audio_and_never_retargets(self):
        self.window_class = "com-nuvio-app-MainKt"
        reply = {
            "manager": list(self.processes[400][:2]),
            "owners": [{"xid": 5, "identity": list(self.processes[200][:2])}],
        }
        with patch("x11_owners.query", return_value=reply) as query:
            playback = self.objects.pop(1)
            self.controller.tick()
            self.assertEqual(self.controller.target, self.processes[200])
            self.clock.return_value = 600
            self.controller.tick()
            self.assertEqual(self.events("choosing"), [])
            self.assertEqual(self.controller.route.feeds, {})
            self.objects.append(playback)
            self.controller.tick()
            self.assertEqual(self.controller.last["backend"], "niri-xres")
            self.assertEqual(set(self.controller.route.feeds), {("pw", "61")})
            query.assert_called_once_with(self.window_class)
            self.processes[200] = (200, "reused", 1)
            self.controller.tick()
            self.assertEqual(self.controller.last["state"], "unavailable")
            self.assertIsNone(self.controller.route)
            query.assert_called_once()

    def test_xres_distinct_instances_missing_dependency_or_wrong_server_ask(self):
        self.window_class = "com-nuvio-app-MainKt"
        for result in (
            None,
            {"manager": [999, "other"], "owners": [{"identity": [200, "nuvio"]}]},
            {
                "manager": [400, "xwayland-proxy"],
                "owners": [
                    {"identity": [200, "nuvio"]},
                    {"identity": [300, "other-app"]},
                ],
            },
        ):
            with self.subTest(result=result), patch("x11_owners.query", return_value=result):
                self.output.seek(0)
                self.output.truncate()
                self.controller.update({"generation": 2, "enabled": True, "picker": "inline"})
                self.open_uncertain_picker()

    def test_xres_cannot_override_manual_cancellation(self):
        self.window_class = "com-nuvio-app-MainKt"
        with patch("x11_owners.query", return_value=None) as query:
            self.open_uncertain_picker()
            self.reply("")
            query.return_value = {
                "manager": [400, "xwayland-proxy"],
                "owners": [
                    {"identity": [200, "nuvio"]},
                ],
            }
            self.controller.tick()
            query.assert_called_once()
            self.assertIsNone(self.controller.route)

    def test_new_source_silences_previous_app_before_xres_query(self):
        self.window_pid = 300
        self.controller.tick()
        previous = self.controller.route
        self.assertTrue(previous.feeds)
        self.source.clear()
        self.source.add((42, "101"))
        self.window_pid = 400
        self.window_class = "com-nuvio-app-MainKt"

        def query(_identifier):
            self.assertIsNone(self.controller.route)
            self.assertEqual(previous.feeds, {})
            self.assertEqual(self.controller.last["state"], "waiting")
            return {"manager": [400, "xwayland-proxy"], "owners": [{"identity": [200, "nuvio"]}]}

        with patch("x11_owners.query", side_effect=query):
            self.controller.tick()
        self.assertEqual(set(self.controller.route.feeds), {("pw", "61")})

    def test_changed_proxy_identity_silences_route_before_rechecking(self):
        self.window_class = "com-nuvio-app-MainKt"
        result = {"manager": [400, "xwayland-proxy"], "owners": [{"identity": [200, "nuvio"]}]}
        with patch("x11_owners.query", return_value=result):
            self.controller.tick()
        previous = self.controller.route
        self.window_class = "changed.Class"
        with patch("x11_owners.query", side_effect=lambda _: self.assertEqual(previous.feeds, {})):
            self.controller.tick()
        self.assertTrue(self.controller.pending_choice)
        self.assertIsNone(self.controller.route)

    def test_share_changed_during_xres_query_never_routes_old_owner(self):
        self.window_class = "com-nuvio-app-MainKt"
        result = {"manager": [400, "xwayland-proxy"], "owners": [{"identity": [200, "nuvio"]}]}

        def changed_source(_identifier):
            self.source.clear()
            self.source.add((42, "101"))
            return result

        with patch("x11_owners.query", side_effect=changed_source):
            self.controller.tick()
        self.assertIsNone(self.controller.route)
        self.assertEqual(self.routes, [])

    def test_window_changed_during_xres_query_never_routes_old_owner(self):
        self.window_class = "com-nuvio-app-MainKt"
        result = {"manager": [400, "xwayland-proxy"], "owners": [{"identity": [200, "nuvio"]}]}

        def changed_window(_identifier):
            self.window_pid = 300
            return result

        with patch("x11_owners.query", side_effect=changed_window):
            self.controller.tick()
        self.assertIsNone(self.controller.route)
        self.assertEqual(self.routes, [])

    def test_niri_failure_never_falls_back_to_a_different_audible_hint(self):
        self.window_pid = 200
        self.controller.tick()
        self.controller.hint = 300
        with patch.object(audio, "niri_request", side_effect=OSError("socket gone")):
            self.clock.return_value = 20
            self.controller.tick()
        self.assertTrue(self.controller.pending_choice)
        self.assertIsNone(self.controller.route)

    def test_unreadable_executable_identity_requires_manual_choice(self):
        self.window_pid = 200
        with patch.object(audio, "capture_proxy", return_value=None):
            self.open_uncertain_picker()

    def test_manual_route_survives_ticks_and_recovers_without_reselection(self):
        self.open_uncertain_picker()
        self.reply("200")
        first = self.controller.route
        self.controller.tick()
        self.assertIs(self.controller.route, first)
        self.controller.suspend(state="unavailable")
        self.window_pid = 300
        self.controller.tick()
        self.assertIsNot(self.controller.route, first)
        self.assertEqual(self.controller.target, self.processes[200])
        self.assertEqual(len(self.events("choosing")), 1)

    def test_cancellation_survives_graph_failures_and_temporary_missing_video(self):
        self.open_uncertain_picker()
        self.reply("")
        self.window_pid = 200
        self.controller.suspend(state="unavailable")
        self.controller.tick()
        self.source.clear()
        self.controller.tick()
        self.source.add((42, "100"))
        self.controller.tick()
        self.assertIsNone(self.controller.route)
        self.assertEqual(len(self.events("choosing")), 1)
        self.assertEqual(self.events("ready"), [])

    def test_failed_pending_picker_reopens_with_a_new_request_and_rejects_old_reply(self):
        self.open_uncertain_picker()
        old_request = self.controller.request
        self.controller.suspend(state="unavailable")
        self.window_pid = 200
        self.controller.tick()
        self.assertGreater(self.controller.request, old_request)
        self.controller.update({"generation": 1, "request": old_request, "selection": "300"})
        self.assertIsNone(self.controller.choice)
        self.reply("200")
        self.assertEqual(self.controller.target, self.processes[200])

    def test_process_binding_survives_transient_failure_without_pid_reuse(self):
        self.window_pid = 200
        self.controller.tick()
        self.controller.suspend(state="unavailable")
        self.processes[200] = (200, "replacement", 1)
        self.controller.tick()
        self.assertIsNone(self.controller.route)
        self.assertEqual(self.controller.last["state"], "unavailable")

    def test_native_hint_to_proxy_also_asks(self):
        with patch.dict(audio.os.environ, {}, clear=True):
            self.open_uncertain_picker()
            self.reply("200")
            self.assertEqual(set(self.controller.route.feeds), {("pw", "61")})
            self.controller.tick()
            self.assertEqual(self.controller.last["backend"], "selection")

    def test_portal_app_id_resolving_to_proxy_also_asks(self):
        self.controller.hint = 0
        self.objects.append(
            {
                "id": 42,
                "type": "PipeWire:Interface:Node",
                "info": {"props": {"object.serial": "100"}},
            }
        )
        self.controller.set_observations(
            [{"node": 42, "token": 1, "kind": "application", "appId": "fixture.app"}]
        )
        with (
            patch.dict(audio.os.environ, {}, clear=True),
            patch("window_inventory.windows", return_value=[{"pid": 400, "app_id": "fixture.app"}]),
        ):
            self.open_uncertain_picker()
            self.reply("200")
            self.assertEqual(set(self.controller.route.feeds), {("pw", "61")})
            self.controller.tick()
            self.assertEqual(self.controller.last["backend"], "selection")

    def test_silent_native_hint_waits_for_its_app_without_timeout(self):
        self.controller.hint = 200
        self.objects[:] = [node(62, 300)]
        with patch.dict(audio.os.environ, {}, clear=True):
            self.controller.tick()
            self.clock.return_value = 600
            self.controller.tick()
            self.assertEqual(self.events("choosing"), [])
            self.assertEqual(self.controller.route.feeds, {})
            self.objects.append(node(65, 201))
            self.controller.tick()
            self.assertEqual(set(self.controller.route.feeds), {("pw", "65")})

    def test_manual_choice_keeps_process_identity_if_pid_is_reused(self):
        self.open_uncertain_picker()
        self.reply("200")
        self.processes[200] = (200, "replacement", 1)
        self.controller.tick()
        self.assertIsNone(self.controller.route)
        self.assertEqual(self.controller.last["state"], "unavailable")

    def test_confirmed_monitor_with_no_playback_does_not_ask(self):
        self.objects.clear()
        self.pulse.clear()
        with patch.object(
            audio,
            "niri_target",
            return_value={"kind": "system", "key": ("monitor",), "backend": "niri"},
        ):
            self.controller.tick()
            self.assertEqual(self.controller.last["state"], "ready")
            self.assertEqual(self.events("choosing"), [])

    def test_screen_skip_preference_is_independent_and_force_picker_wins(self):
        with patch.object(
            audio,
            "niri_target",
            return_value={"kind": "system", "key": ("monitor",), "backend": "niri"},
        ):
            for auto, skip, force in [
                (True, False, False),
                (False, True, False),
                (True, True, True),
            ]:
                with self.subTest(auto=auto, skip=skip, force=force):
                    self.controller.update(
                        {
                            "generation": 2,
                            "enabled": True,
                            "picker": "inline",
                            "autoDetect": auto,
                            "skipScreenPicker": skip,
                            "forcePicker": force,
                            "hint": 200,
                        }
                    )
                    self.controller.tick()
                    if skip and not force:
                        self.assertEqual(self.controller.last["state"], "ready")
                    else:
                        self.assertTrue(self.controller.pending_choice)
                        self.assertIsNone(self.controller.route)

    def test_window_stays_manual_with_only_screen_skip_enabled(self):
        self.window_pid = 200
        self.controller.update(
            {
                "generation": 2,
                "enabled": True,
                "picker": "inline",
                "autoDetect": False,
                "skipScreenPicker": True,
            }
        )
        self.controller.tick()
        self.assertTrue(self.controller.pending_choice)
        self.assertIsNone(self.controller.route)

    def test_ambiguous_compositor_does_not_fall_back_to_an_audible_hint(self):
        self.controller.hint = 200
        self.clock.return_value = 12
        with patch.object(audio, "niri_target", return_value=None):
            self.controller.tick()
        self.assertTrue(self.controller.pending_choice)
        self.assertIsNone(self.controller.route)

    def test_ambiguous_portal_does_not_fall_back_to_an_audible_hint(self):
        self.controller.hint = 200
        self.controller.set_observations(
            [{"node": 42, "token": 1, "kind": "application", "appId": "unknown"}]
        )
        self.clock.return_value = 12
        with patch.dict(audio.os.environ, {}, clear=True):
            self.controller.tick()
        self.assertTrue(self.controller.pending_choice)
        self.assertIsNone(self.controller.route)


class CaptureProxyIdentity(unittest.TestCase):
    def test_unreadable_executable_is_unknown(self):
        with patch.object(audio.os, "readlink", side_effect=PermissionError()):
            self.assertIsNone(audio.capture_proxy(200))

    def test_proxy_rejection_uses_executable_identity_not_window_or_audio_names(self):
        for executable, expected in [
            ("/usr/bin/xwayland-satellite", True),
            ("/usr/bin/Xwayland (deleted)", True),
            ("/usr/lib/xdg-desktop-portal-gnome", True),
            ("/opt/zen/zen-bin", False),
            ("/tmp/.mount_Nuvio/Nuvio", False),
        ]:
            with (
                self.subTest(executable=executable),
                patch.object(audio.os, "readlink", return_value=executable),
            ):
                self.assertEqual(audio.capture_proxy(200), expected)
