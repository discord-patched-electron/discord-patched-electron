import json
import unittest
from unittest.mock import Mock, patch

from test_wayland_audio import audio


class NiriProtocol(unittest.TestCase):
    def setUp(self):
        self.socket = Mock()
        self.socket.__enter__ = Mock(return_value=self.socket)
        self.socket.__exit__ = Mock(return_value=False)
        self.enterContext(patch.object(audio.socket, "socket", return_value=self.socket))
        self.enterContext(patch.dict(audio.os.environ, {"NIRI_SOCKET": "fixture"}))

    def test_requires_the_requested_reply_tag_and_array(self):
        for value in ({"Ok": {"Casts": []}}, {"Ok": {"Windows": None}}, [], {"Err": "gone"}):
            with self.subTest(value=value):
                self.socket.recv.return_value = (json.dumps(value) + "\n").encode()
                with self.assertRaises(ValueError):
                    audio.niri_request("Windows")
        self.socket.recv.side_effect = [b'{"Ok":{"Win', b'dows":[]}}\n']
        self.assertEqual(audio.niri_request("Windows"), [])

    def test_trickled_reply_cannot_extend_total_deadline(self):
        self.socket.recv.return_value = b" "
        with patch.object(audio.time, "monotonic", side_effect=[0, 0.1, 0.6, 1.1]):
            with self.assertRaises(TimeoutError):
                audio.niri_request("Windows")
        self.assertEqual(self.socket.recv.call_count, 2)
        self.assertLess(self.socket.settimeout.call_args.args[0], 0.5)


class NiriTargetShapes(unittest.TestCase):
    def setUp(self):
        self.cast = {
            "pw_node_id": 20,
            "session_id": 1,
            "stream_id": 2,
            "is_active": True,
            "target": {"Window": {"id": 7}},
        }

    def test_malformed_or_mixed_target_never_becomes_system_audio(self):
        for target in (
            None,
            [],
            {"Window": None},
            {"Output": None},
            {"Output": {}},
            {"Output": {"name": ""}},
            {"Output": {"name": "screen"}, "Window": {"id": 7}},
        ):
            with self.subTest(target=target):
                self.assertIsNone(
                    audio.niri_target({(20, "s")}, [{**self.cast, "target": target}], [])
                )

    def test_non_arrays_and_bad_rows_require_manual_selection(self):
        for casts, windows in ((None, []), ([None], []), ([self.cast], [None]), ({}, [])):
            with self.subTest(casts=casts, windows=windows):
                self.assertIsNone(audio.niri_target({(20, "s")}, casts, windows))

    def test_duplicate_window_identity_cannot_discard_bad_owner(self):
        self.assertIsNone(
            audio.niri_target(
                {(20, "s")},
                [self.cast],
                [{"id": 7, "pid": 200}, {"id": 7, "pid": None}],
            )
        )

    def test_truthy_active_string_does_not_count_as_confirmation(self):
        self.assertIsNone(
            audio.niri_target(
                {(20, "s")},
                [{**self.cast, "is_active": "false"}],
                [{"id": 7, "pid": 200}],
            )
        )
