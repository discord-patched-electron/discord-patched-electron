"""Exact compositor identity for picker-free cursor capture."""

import importlib.util
import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "cursor_broker", ROOT / "integration/video/vkcapture-broker.py"
)
broker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(broker)


class CursorIdentity(unittest.TestCase):
    def resolve(self, nodes, identities=("identity", "identity")):
        with (
            mock.patch.dict(
                os.environ, {"SWAYSOCK": "/synthetic", "WAYLAND_DISPLAY": "/test"}, clear=True
            ),
            mock.patch.object(broker, "process_identity", side_effect=identities),
            mock.patch.object(broker, "compositor_peer_pid", return_value=123),
            mock.patch.object(
                broker.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    [], 0, json.dumps({"nodes": nodes}).encode(), b""
                ),
            ),
        ):
            return broker.sway_cursor_toplevel({"pid": 42, "processIdentity": "identity"})

    def test_exact_unique_process_and_identifier(self):
        self.assertEqual(
            self.resolve([{"pid": 42, "foreign_toplevel_identifier": "opaque-id"}]), "opaque-id"
        )
        self.assertIsNone(
            self.resolve([{"pid": 43, "title": "game", "foreign_toplevel_identifier": "other"}])
        )

    def test_ambiguous_missing_invalid_and_reused_identity_fail_closed(self):
        node = {"pid": 42, "foreign_toplevel_identifier": "id"}
        self.assertIsNone(self.resolve([node, node]))
        self.assertIsNone(self.resolve([node], ("identity", "reused")))
        for identifier in (None, "", "x" * 33, "bad\n", 42):
            self.assertIsNone(
                self.resolve([{"pid": 42, "foreign_toplevel_identifier": identifier}])
            )

    def test_no_ipc_without_current_sway_session(self):
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(broker.subprocess, "run") as run,
        ):
            self.assertIsNone(broker.sway_cursor_toplevel({"pid": 42}))
            run.assert_not_called()

    def test_nested_compositor_identity_mismatch_fails_closed(self):
        with (
            mock.patch.dict(
                os.environ, {"SWAYSOCK": "/outer", "WAYLAND_DISPLAY": "/inner"}, clear=True
            ),
            mock.patch.object(broker, "process_identity", return_value="identity"),
            mock.patch.object(broker, "compositor_peer_pid", side_effect=[123, 456]),
            mock.patch.object(broker.subprocess, "run") as run,
        ):
            self.assertIsNone(
                broker.sway_cursor_toplevel({"pid": 42, "processIdentity": "identity"})
            )
            run.assert_not_called()
