"""Regression/control matrix with the production /proc parser.

All /proc contents, graph/IPC/command responses and routes are synthetic.
Reparenting preserves audio; PID reuse and UID changes still stop it.
"""

import contextlib
import io
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import test_wayland_audio as fixtures

audio = fixtures.audio


class IndependentReparent(unittest.TestCase):
    def test_identity_change_matrix(self):
        for change in ("unchanged", "parent", "start", "uid"):
            with self.subTest(change=change):
                process = {100: [1, "1000", 1000], 200: [500, "2000", 1000]}

                def read_stat(path, process=process):
                    pid = int(path.parts[2])
                    if pid not in process:
                        raise FileNotFoundError()
                    parent, start, _uid = process[pid]
                    fields = ["0"] * 20
                    fields[0], fields[1], fields[19] = "S", str(parent), start
                    return f"{pid} (synthetic app) " + " ".join(fields)

                def stat(path, process=process):
                    pid = int(path.parts[2])
                    if pid not in process:
                        raise FileNotFoundError()
                    return SimpleNamespace(st_uid=process[pid][2])

                objects = [
                    {
                        "id": 11,
                        "type": "PipeWire:Interface:Node",
                        "info": {
                            "props": {
                                "media.class": "Stream/Input/Video",
                                "application.process.id": 100,
                            }
                        },
                    },
                    {
                        "id": 20,
                        "type": "PipeWire:Interface:Node",
                        "info": {
                            "props": {
                                "object.serial": "300",
                            }
                        },
                    },
                    {
                        "id": 30,
                        "type": "PipeWire:Interface:Link",
                        "info": {
                            "input-node-id": 11,
                            "output-node-id": 20,
                            "state": "active",
                        },
                    },
                ]
                replies = {
                    "Casts": [
                        {
                            "pw_node_id": 20,
                            "session_id": 1,
                            "stream_id": 2,
                            "is_active": True,
                            "target": {"Window": {"id": 7}},
                        }
                    ],
                    "Windows": [{"id": 7, "pid": 200}],
                }
                routes = []

                def route(routes=routes):
                    result = Mock(capture_pid=900)
                    routes.append(result)
                    return result

                with (
                    patch.object(audio.Path, "read_text", read_stat),
                    patch.object(audio.Path, "stat", stat),
                    patch.object(audio.os, "getuid", return_value=1000),
                    patch.object(audio, "discord_root", return_value=(100, "1000", 1)),
                    patch.object(audio, "graph", return_value=objects),
                    patch.object(audio, "command", return_value="[]"),
                    patch.object(audio, "niri_request", side_effect=replies.__getitem__),
                    patch.object(audio, "Route", side_effect=route),
                    patch.dict(audio.os.environ, {"NIRI_SOCKET": "synthetic"}, clear=True),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    controller = audio.Controller(100)
                    controller.update({"generation": 1, "enabled": True, "hint": 0})
                    controller.tick()
                    self.assertEqual(controller.last["state"], "ready")
                    before = audio.process_identity(200)
                    if change == "parent":
                        process[200][0] = 1
                        self.assertEqual(before[:2], audio.process_identity(200)[:2])
                        self.assertTrue(audio.descendant(200, before))
                    elif change == "start":
                        process[200][1] = "2001"
                    elif change == "uid":
                        process[200][2] = 1001
                    for _ in range(3):
                        controller.tick()
                    if change in ("unchanged", "parent"):
                        self.assertEqual(controller.last["state"], "ready")
                        routes[0].close.assert_not_called()
                    else:
                        self.assertIsNone(controller.route)
                        routes[0].close.assert_called_once()
                        expected = "waiting" if change == "uid" else "unavailable"
                        self.assertEqual(controller.last["state"], expected)
                    if change == "parent":
                        # Recovery is bounded to a generation, not permanently lost.
                        controller.update({"generation": 2, "enabled": True, "hint": 0})
                        controller.tick()
                        self.assertEqual(controller.last["state"], "ready")
                        self.assertEqual(len(routes), 2)
                    controller.close()
