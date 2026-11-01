import subprocess
import sys
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration/audio"))

import x11_owners as x11
from test_wayland_audio import audio


class X11Owners(unittest.TestCase):
    def setUp(self):
        self.processes = {
            100: (100, "proxy", 1),
            200: (200, "app", 1),
            201: (201, "child", 200),
            300: (300, "other", 1),
        }
        self.enterContext(patch.object(audio, "process_identity", side_effect=self.processes.get))
        self.result = {
            "manager": [100, "proxy"],
            "owners": [{"identity": [200, "app"]}, {"identity": [201, "child"]}],
        }
        self.query = self.enterContext(patch.object(x11, "query", return_value=self.result))

    def resolve(self):
        return x11.resolve("exact.Class", self.processes[100], self.processes.get, audio.descendant)

    def test_same_owner_and_children_are_one_family(self):
        self.assertEqual(self.resolve(), self.processes[200])
        self.result["owners"].append({"identity": [200, "app"]})
        self.assertEqual(self.resolve(), self.processes[200])

    def test_unrelated_instance_cannot_be_discarded(self):
        self.result["owners"].append({"identity": [300, "other"]})
        self.assertIsNone(self.resolve())

    def test_missing_reused_or_foreign_process_invalidates_whole_result(self):
        for identity in ([200, "reused"], [999, "gone"], None, [True, "app"]):
            with self.subTest(identity=identity):
                self.result["owners"][0]["identity"] = identity
                self.assertIsNone(self.resolve())

    def test_selected_proxy_must_manage_this_display_and_still_be_live(self):
        for manager in ([300, "other"], [100, "reused"], None):
            with self.subTest(manager=manager):
                self.result["manager"] = manager
                self.assertIsNone(self.resolve())

    def test_never_resolve_to_proxy_or_its_children(self):
        self.result["owners"] = [{"identity": [100, "proxy"]}]
        self.assertIsNone(self.resolve())
        self.processes[200] = (200, "app", 100)
        self.result["owners"] = [{"identity": [200, "app"]}]
        self.assertIsNone(self.resolve())

    def test_invalid_probe_results_fail_closed(self):
        for result in (None, [], {}, {"manager": [100, "proxy"], "owners": []}):
            with self.subTest(result=result):
                self.query.return_value = result
                self.assertIsNone(self.resolve())


class X11ProbeBoundary(unittest.TestCase):
    def test_remote_or_missing_displays_and_invalid_classes_do_not_connect(self):
        for display, identifier in (
            ("host:0", "app"),
            ("localhost:10", "app"),
            ("", "app"),
            (":0", None),
            (":0", ""),
            (":0", "x" * 513),
        ):
            with (
                self.subTest(display=display, identifier=identifier),
                patch.dict(x11.os.environ, {"DISPLAY": display}),
                patch.object(x11.subprocess, "run") as run,
            ):
                self.assertIsNone(x11.query(identifier))
                run.assert_not_called()

    def test_hung_missing_or_crashed_probe_falls_back(self):
        for error in (
            FileNotFoundError(),
            subprocess.TimeoutExpired("probe", 2),
            subprocess.CalledProcessError(-11, "probe"),
        ):
            with (
                self.subTest(error=error),
                patch.dict(x11.os.environ, {"DISPLAY": ":0"}),
                patch.object(x11.subprocess, "run", side_effect=error) as run,
            ):
                self.assertIsNone(x11.query("app"))
                self.assertEqual(run.call_args.kwargs["timeout"], 2)

    def test_malformed_or_oversized_output_falls_back(self):
        for output in ("invalid-json", " " * (1024 * 1024 + 1)):
            with (
                self.subTest(size=len(output)),
                patch.dict(x11.os.environ, {"DISPLAY": ":0"}),
                patch.object(x11.subprocess, "run", return_value=Mock(stdout=output)),
            ):
                self.assertIsNone(x11.query("app"))

    def test_missing_library_is_a_probe_failure(self):
        with patch.object(x11.C, "CDLL", side_effect=OSError("missing libxcb-res")):
            with self.assertRaises(OSError):
                x11.XConnection(":0")


class X11Snapshots(unittest.TestCase):
    def test_a_later_class_change_cannot_hide_a_second_matching_owner(self):
        connection = x11.XConnection.__new__(x11.XConnection)
        connection.connection = None
        connection.res = Mock()
        version = x11.Version(major=1, minor=2)
        connection.reply = Mock(return_value=nullcontext(x11.C.addressof(version)))
        connection.manager = Mock(return_value=[100, "proxy"])
        connection.windows = Mock(return_value=[5, 6])
        connection.owner = Mock(side_effect=lambda xid: [xid * 100, "start"])
        # Window 5 was not a match when first read. It changes class while
        # window 6 is read, without changing the root's window-ID inventory.
        connection.window_class = Mock(side_effect=["other", "wanted", "wanted", "wanted"])
        with self.assertRaisesRegex(ValueError, "ownership changed"):
            connection.probe("wanted")

    def test_restacking_alone_is_not_an_ownership_change(self):
        connection = x11.XConnection.__new__(x11.XConnection)
        connection.connection = None
        connection.res = Mock()
        version = x11.Version(major=1, minor=2)
        connection.reply = Mock(return_value=nullcontext(x11.C.addressof(version)))
        connection.manager = Mock(return_value=[100, "proxy"])
        connection.windows = Mock(side_effect=[[5, 6], [6, 5], [6, 5], [5, 6]])
        connection.owner = Mock(return_value=[200, "app"])
        connection.window_class = Mock(return_value="wanted")
        self.assertEqual(len(connection.probe("wanted")["owners"]), 2)
