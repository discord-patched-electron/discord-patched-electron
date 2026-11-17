"""Cold portable startup must leave time for login after module preparation."""

import importlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_desktop_launch as launch
from common import write_json


class DesktopLaunch(unittest.TestCase):
    def test_slow_cold_modules_can_reach_login_without_extending_local_launches(self):
        for distribution, login_at, setup_at, setup_persisted, passed in (
            ("module-only", 130, None, True, True),
            ("local-test-only", 130, None, True, False),
            ("module-only", 350, 290, True, True),
            ("module-only", 450, 290, True, False),
            ("module-only", 295, 290, False, False),
        ):
            with (
                self.subTest(distribution=distribution, login_at=login_at),
                tempfile.TemporaryDirectory() as temp,
            ):
                root = Path(temp)
                app = root / "app"
                app.mkdir()
                write_json(
                    app / "local-build.json",
                    {"channel": "canary", "client_distribution": distribution},
                )
                clock = [0]
                profile = root / "profile"
                setup_sent = [False]

                def sleep(seconds, clock=clock):
                    clock[0] += seconds

                def urlopen(
                    url,
                    clock=clock,
                    setup_at=setup_at,
                    login_at=login_at,
                    setup_sent=setup_sent,
                    setup_persisted=setup_persisted,
                    profile=profile,
                    **kwargs,
                ):
                    if url.endswith("/json/version"):
                        return io.StringIO(json.dumps({"webSocketDebuggerUrl": "ws://browser"}))
                    targets = []
                    if setup_at is not None and clock[0] >= setup_at and not setup_sent[0]:
                        setup_sent[0] = True
                        write_json(
                            profile / "dpe-updater/mods.json", {"setupComplete": setup_persisted}
                        )
                        targets = [
                            {
                                "url": "dpe-splash://welcome/config.html?mode=welcome",
                                "webSocketDebuggerUrl": "ws://welcome",
                            }
                        ]
                    elif clock[0] >= login_at:
                        targets = [
                            {
                                "type": "page",
                                "url": "https://canary.discord.com/login",
                                "webSocketDebuggerUrl": "ws://page",
                            }
                        ]
                    return io.StringIO(json.dumps(targets))

                process = Mock()
                process.poll.return_value = None
                page = {
                    "discordNative": True,
                    "ready": "complete",
                    "loginForm": True,
                    "passwordField": True,
                }
                with (
                    patch.object(launch.subprocess, "Popen", return_value=process),
                    patch.object(launch.subprocess, "run"),
                    patch.object(
                        launch.time, "monotonic", side_effect=lambda clock=clock: clock[0]
                    ),
                    patch.object(launch.time, "sleep", side_effect=sleep),
                    patch.object(launch.urllib.request, "urlopen", side_effect=urlopen),
                    patch.object(launch, "inspect_discord", return_value=page),
                    patch.object(launch, "complete_setup", return_value={"doneSent": True}),
                    patch.object(launch, "verify_codec", return_value=None),
                    patch.object(launch, "check_popout", return_value={"passed": True}),
                    patch.object(
                        launch,
                        "cdp",
                        return_value={"processInfo": [{"type": "browser", "id": os.getpid()}]},
                    ),
                    patch("sys.stdout", new_callable=io.StringIO),
                ):
                    report = launch.check(app, root / "report", profile=profile)
                self.assertEqual(report["passed"], passed)
                self.assertEqual(report["timed_out"], not passed)
                self.assertLessEqual(
                    report["startup_elapsed_seconds"], 411 if setup_at and setup_persisted else 300
                )

    def test_sequence_reuses_then_removes_profile_on_success_and_failure(self):
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as temp:
                output = Path(temp) / "launch"
                args = SimpleNamespace(
                    app=Path(temp),
                    output=output,
                    equicord=False,
                    read_only_app=True,
                    runtime_archive=None,
                    application_archive=None,
                    expect_setup=False,
                    setup_configure=False,
                    launcher=None,
                    relaunch=True,
                )
                seen = []

                def check(app, output, profile=None, *args, seen=seen, fail=fail, **kwargs):
                    self.assertIsNotNone(profile)
                    if seen:
                        self.assertEqual(profile, seen[0])
                        self.assertTrue((profile / "fixture").exists())
                    else:
                        profile.mkdir()
                        (profile / "fixture").touch()
                    seen.append(profile)
                    if fail:
                        raise RuntimeError("fixture startup failed")
                    return {"passed": True, "profile": str(profile), "setup_seen": False}

                with patch.object(launch, "check", side_effect=check):
                    if fail:
                        with self.assertRaisesRegex(RuntimeError, "fixture startup failed"):
                            launch.check_sequence(args)
                    else:
                        self.assertTrue(launch.check_sequence(args))
                        self.assertEqual(len(seen), 2)
                self.assertFalse(seen[0].parent.exists())

    def test_multi_launch_fixture_cleans_profile_after_failure(self):
        for module_name in ("check_client_mods", "check_application_update"):
            with self.subTest(module=module_name), tempfile.TemporaryDirectory() as temp:
                module = importlib.import_module(module_name)
                output = Path(temp) / "report"

                def fail(*args, output=output):
                    (output / "profile").mkdir(parents=True)
                    (output / "console.log").write_text("retained diagnostics")
                    raise RuntimeError("fixture failed")

                with patch.object(module, "_verify", side_effect=fail):
                    with self.assertRaisesRegex(RuntimeError, "fixture failed"):
                        module.verify(Path(temp), output, {})
                self.assertFalse((output / "profile").exists())
                self.assertEqual((output / "console.log").read_text(), "retained diagnostics")

    def test_popouts_wait_for_exact_target_destruction_and_fail_on_stalled_close(self):
        for stalled in (False, True):
            with self.subTest(stalled=stalled):
                state = {"alive": None, "closing": False, "polls": 0, "opened": 0}
                clock = [0.0]

                def protocol(
                    websocket, method, params=None, *, timeout=5, state=state, stalled=stalled
                ):
                    if method == "Target.getTargets":
                        infos = [{"targetId": "parent", "type": "page"}]
                        if state["alive"] is not None:
                            if state["closing"]:
                                state["polls"] += 1
                                if not stalled and state["polls"] >= 4:
                                    state["alive"] = None
                            if state["alive"] is not None:
                                infos.append({"targetId": state["alive"], "type": "page"})
                        return {"targetInfos": infos}
                    if params.get("awaitPromise"):
                        self.assertIsNone(
                            state["alive"], "Reopened before native target destruction"
                        )
                        state.update(alive=f"child-{state['opened']}", closing=False, polls=0)
                        state["opened"] += 1
                        return {
                            "result": {"value": {"passed": True, "attempts": [{"passed": True}]}}
                        }
                    state["closing"] = True
                    return {}

                def sleep(seconds, clock=clock):
                    clock[0] += seconds

                with (
                    patch.object(launch, "cdp", side_effect=protocol),
                    patch.object(
                        launch.time, "monotonic", side_effect=lambda clock=clock: clock[0]
                    ),
                    patch.object(launch.time, "sleep", side_effect=sleep),
                ):
                    report = launch.check_popout("ws://fixture")
                self.assertEqual(report["passed"], not stalled)
                self.assertEqual(state["opened"], 1 if stalled else 4)
                if stalled:
                    self.assertIn("not destroyed", report["error"])

    def test_devtools_transport_protocol_and_javascript_errors_are_not_empty_success(self):
        for response in (
            SimpleNamespace(returncode=2, stdout="", stderr="connection closed"),
            SimpleNamespace(
                returncode=0, stdout=json.dumps({"error": {"message": "missing target"}}), stderr=""
            ),
            SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"result": {"exceptionDetails": {"text": "JS failed"}}}),
                stderr="",
            ),
        ):
            with (
                self.subTest(response=response),
                patch.object(launch.subprocess, "run", return_value=response),
            ):
                with self.assertRaises(ValueError):
                    launch.cdp("ws://fixture", "Runtime.evaluate")

    def test_setup_readiness_acknowledgement_survives_target_closing_during_dispatch(self):
        ready = {"welcome": True, "doneSent": True}
        with patch.object(
            launch,
            "cdp",
            side_effect=[{"result": {"value": json.dumps(ready)}}, ValueError("target closed")],
        ):
            state = launch.complete_setup("ws://welcome")
        self.assertTrue(state["doneSent"])
        self.assertIn("target closed", state["dispatch_error"])
