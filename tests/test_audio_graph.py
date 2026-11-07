"""Notification ordering and cache invalidation without audio or host services."""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration/audio"))
from audio_graph import AudioGraph, GraphState, PulseEvents


class GraphUpdates(unittest.TestCase):
    def test_incremental_properties_links_removal_and_id_reuse(self):
        state = GraphState()
        state.feed(
            json.dumps(
                [
                    {
                        "id": 1,
                        "type": "PipeWire:Interface:Node",
                        "info": {
                            "props": {"old": "removed", "object.serial": 4},
                            "state": "running",
                            "params": {"unused": [1, 2, 3]},
                        },
                    },
                    {
                        "id": 2,
                        "type": "PipeWire:Interface:Link",
                        "info": {"input-node-id": 1, "output-node-id": 3, "state": "active"},
                    },
                ]
            ).encode()
        )
        state.feed(
            b'[{"id":1,"info":{"props":{"object.serial":4}}}][{"id":2,"info":{"state":"paused"}}]'
        )
        self.assertEqual(
            state.objects[1]["info"], {"props": {"object.serial": 4}, "state": "running"}
        )
        self.assertEqual(state.objects[2]["info"]["input-node-id"], 1)
        self.assertEqual(state.objects[2]["info"]["state"], "paused")
        state.feed(
            b'[{"id":1,"info":null}][{"id":1,"type":"PipeWire:Interface:Client","info":{"props":{"object.serial":99}}}]'
        )
        self.assertEqual(state.objects[1]["info"], {"props": {"object.serial": 99}})
        state.feed(b'[{"id":1,"type":"PipeWire:Interface:Factory","info":{}}]')
        self.assertNotIn(1, state.objects)

    def test_split_unicode_and_multiple_json_arrays(self):
        state = GraphState()
        data = json.dumps(
            [
                {
                    "id": 1,
                    "type": "PipeWire:Interface:Client",
                    "info": {"props": {"application.name": "日本語"}},
                }
            ],
            ensure_ascii=False,
        ).encode()
        for byte in data:
            state.feed(bytes([byte]))
        self.assertTrue(state.ready)
        self.assertEqual(state.objects[1]["info"]["props"]["application.name"], "日本語")
        state.feed(b'\n[]\n[{"id":1,"info":null}]\n')
        self.assertEqual(state.objects, {})

    def test_invalid_and_unbounded_graphs_are_rejected(self):
        for payload in [
            b"{}",
            b'[{"id":true}]',
            b'[{"id":-1}]',
            b'[{"id":1,"type":"PipeWire:Interface:Node","info":{"props":null}}]',
            b" " * (8 * 1024 * 1024 + 1),
        ]:
            with self.subTest(size=len(payload)), self.assertRaises(ValueError):
                GraphState().feed(payload)

    def test_introspection_client_events_do_not_create_a_refresh_loop(self):
        state = PulseEvents()
        state.inputs_dirty = state.sinks_dirty = False
        state.feed(b"Event 'new' on cli")
        self.assertFalse(state.ready)
        state.feed(b"ent #100\nEvent 'remove' on client #100\n")
        self.assertTrue(state.ready)
        self.assertFalse(state.inputs_dirty)
        self.assertFalse(state.sinks_dirty)
        state.feed(b"Event 'change' on sink-input #15\n")
        self.assertTrue(state.inputs_dirty)
        self.assertFalse(state.sinks_dirty)
        state.inputs_dirty = False
        state.feed(b"Event 'remove' on sink #4\n")
        self.assertTrue(state.inputs_dirty)
        self.assertTrue(state.sinks_dirty)


class Child:
    def __init__(self):
        read, self.write = os.pipe()
        self.stdout = os.fdopen(read, "rb", buffering=0)
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        if self.returncode is None:
            os.close(self.write)
            self.returncode = 0

    kill = terminate

    def wait(self, timeout=None):
        return self.returncode


class SubscriptionLifecycle(unittest.TestCase):
    def setUp(self):
        self.children, self.commands = [], []
        self.inputs = [{"index": 1}]
        self.events = AudioGraph(self.launch, self.command)

    def tearDown(self):
        self.events.close()

    def launch(self, args):
        child = Child()
        self.children.append(child)
        if "pw-dump" in args[0]:
            os.write(
                child.write,
                b'[{"id":1,"type":"PipeWire:Interface:Node","info":{"props":{"object.serial":7}}}]',
            )
        return child

    def command(self, args):
        self.commands.append(args)
        if args[-1] == "info":
            os.write(self.events.children[1].write, b"Event 'new' on client #123\n")
            return "read-only subscription barrier"
        # First snapshot must follow observed subscription readiness.
        self.assertTrue(self.events.pulse.ready)
        return json.dumps(self.inputs if args[-1] == "sink-inputs" else [{"index": 4}])

    def test_steady_snapshots_reuse_cache_and_events_refresh_only_the_affected_view(self):
        first = self.events.snapshot()
        self.assertEqual(first[1], [{"index": 1}])
        count = len(self.commands)
        for _ in range(10):
            self.assertEqual(self.events.snapshot(), first)
        self.assertEqual(len(self.commands), count)
        self.inputs = [{"index": 2}]
        os.write(
            self.children[1].write,
            b"Event 'remove' on sink-input #1\nEvent 'new' on sink-input #2\n",
        )
        self.assertEqual(self.events.snapshot()[1], self.inputs)
        self.assertEqual(len(self.commands), count + 1)
        self.events.sink_snapshot()
        count = len(self.commands)
        os.write(self.children[1].write, b"Event 'change' on sink-input #2\n")
        self.events.sink_snapshot()
        self.assertEqual(len(self.commands), count)

    def test_watcher_loss_discards_old_graph_and_pulse_snapshots(self):
        self.events.snapshot()
        self.children[0].terminate()
        with self.assertRaisesRegex(ValueError, "ended"):
            self.events.snapshot()
        self.assertEqual(self.events.graph.objects, {})
        self.assertIsNone(self.events.inputs)
        self.assertFalse(self.events.children)
        self.inputs = [{"index": 9}]
        self.assertEqual(self.events.snapshot()[1], self.inputs)

    def test_subscription_never_uses_an_unconfirmed_initial_snapshot(self):
        with patch("audio_graph.time.monotonic", side_effect=[0, 4]):
            with self.assertRaisesRegex(ValueError, "ready"):
                self.events.snapshot()
        self.assertFalse(self.events.children)

    def test_partial_update_cannot_leave_a_stale_route_snapshot(self):
        self.events.snapshot()
        os.write(self.children[0].write, b'[{"id":1,"info":')
        with patch("audio_graph.time.monotonic", side_effect=[0, 4]):
            with self.assertRaisesRegex(ValueError, "ready"):
                self.events.snapshot()
        self.assertFalse(self.events.children)
        self.assertIsNone(self.events.inputs)
