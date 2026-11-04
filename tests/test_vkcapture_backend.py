import importlib.util
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_vkcapture_backend import build


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "integration/video" / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


broker = module("dpe_game_broker_test", "vkcapture-broker.py")
launcher = module("dpe_game_launcher_test", "gamecapture-launch.py")
command = module("dpe_game_command_test", "gamecapture-command.py")


class GameCapture(unittest.TestCase):
    def test_literal_launcher_placeholders_explain_steam_substitution(self):
        for placeholder in ("game-command", "%command%"):
            with self.subTest(placeholder=placeholder):
                with self.assertRaisesRegex(ValueError, "Steam launch options only"):
                    command.launch(["--", placeholder], {})
                with mock.patch.object(sys, "argv", ["launcher", "--", placeholder]):
                    with self.assertRaisesRegex(ValueError, "Steam launch options only"):
                        launcher.main()

    @unittest.skipUnless(shutil.which("cc"), "native development tools")
    def test_vanilla_texture_protocol_without_gpu(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "compat"
            subprocess.run(
                [
                    "cc",
                    "-std=gnu11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    str(ROOT / "tests/screenshare/vkcapture-compat-check.c"),
                    "-o",
                    str(executable),
                ],
                check=True,
            )
            subprocess.run([str(executable)], check=True, timeout=5, capture_output=True)

    def test_explicit_stock_compatibility_launch(self):
        for vulkan in (False, True):
            env = {"OBS_COMPAT": "1", **({"OBS_VKCAPTURE": "1"} if vulkan else {})}
            with (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch.object(sys, "argv", ["launcher", "--", "game", "arg"]),
                mock.patch.object(
                    launcher.shutil, "which", return_value="/usr/bin/obs-gamecapture"
                ),
                mock.patch.object(launcher.os, "execvpe") as execute,
            ):
                launcher.main()
                expected = (
                    ["game", "arg"] if vulkan else ["/usr/bin/obs-gamecapture", "game", "arg"]
                )
                execute.assert_called_once_with(expected[0], expected, env)
        with (
            mock.patch.dict(os.environ, {"OBS_COMPAT": "1"}, clear=True),
            mock.patch.object(sys, "argv", ["launcher", "--", "game"]),
            mock.patch.object(launcher.shutil, "which", return_value=None),
        ):
            with self.assertRaisesRegex(ValueError, "requires the installed"):
                launcher.main()

    def test_existing_vanilla_hooks_do_not_load_a_second_interposer(self):
        for environment in (
            {"OBS_COMPAT": "1", "VK_INSTANCE_LAYERS": "existing_layer"},
            {"LD_PRELOAD": "/usr/lib/libobs_glcapture.so:another.so"},
        ):
            self.assertEqual(
                launcher.launch_environment("/missing", "/unused", environment), environment
            )

    def test_vanilla_hello_handles_stream_fragments_and_rejects_descriptors(self):
        import array

        left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        packet = b"\x0aMinecraft\0" + bytes(117)
        self.assertEqual(len(packet), 128)
        buffered = bytearray()
        left.send(packet[:5])
        self.assertIsNone(broker.obs_hello(right, buffered))
        left.send(packet[5:])
        self.assertEqual(broker.obs_hello(right, buffered)["name"], "Minecraft")
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            left.sendmsg([packet], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [fd]))])
            with self.assertRaises(ValueError):
                broker.obs_hello(right, bytearray())
        finally:
            os.close(fd)

    def test_vanilla_listener_does_not_replace_an_existing_obs_listener(self):
        with tempfile.TemporaryDirectory() as directory:
            endpoint = str(Path(directory) / "obs.sock")
            obs = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            obs.bind(endpoint)
            obs.listen(1)
            self.addCleanup(obs.close)
            instance = broker.Broker(Path(directory) / "dpe.sock", "/bin/true", "auto", endpoint)
            self.addCleanup(instance.close)
            instance.listen_compat()
            self.assertEqual(instance.compat_status, "in-use")
            self.assertIsNone(instance.compat_server)
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.addCleanup(client.close)
            client.connect(endpoint)
            accepted, _ = obs.accept()
            accepted.close()

    def test_vanilla_control_uses_copy_without_a_dpe_device_handshake(self):
        with tempfile.TemporaryDirectory() as directory:
            instance = broker.Broker(Path(directory) / "dpe.sock", "/bin/true", "auto")
            self.addCleanup(instance.close)
            left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
            self.addCleanup(left.close)
            self.addCleanup(right.close)
            peer = {"protocol": "obs", "socket": right, "uuid": None}
            self.assertEqual(instance.child_arguments(peer)[-2:], ["--hook-protocol", "obs"])
            self.assertNotIn("--device-uuid", instance.child_arguments(peer))
            with mock.patch.dict(os.environ, {"DPE_GAME_CAPTURE_HANDOFF": "relay"}):
                self.assertEqual(
                    instance.child_environment(peer)["DPE_GAME_CAPTURE_HANDOFF"], "copy"
                )

    def test_producer_keeps_cursor_display_credentials_without_inheriting_hooks(self):
        with tempfile.TemporaryDirectory() as directory:
            instance = broker.Broker(Path(directory) / "dpe.sock", "/bin/true", "auto")
            self.addCleanup(instance.close)
            values = {
                "DISPLAY": ":123",
                "XAUTHORITY": "/private/display-authority",
                "LD_PRELOAD": "/untrusted/game-hook.so",
                "VK_INSTANCE_LAYERS": "game-layer",
            }
            with mock.patch.dict(os.environ, values, clear=True):
                env = instance.child_environment({"protocol": "dpe", "direct": True})
            self.assertEqual(env["DISPLAY"], values["DISPLAY"])
            self.assertEqual(env["XAUTHORITY"], values["XAUTHORITY"])
            self.assertEqual(env["DPE_GAME_CAPTURE_HANDOFF"], "auto")
            self.assertNotIn("LD_PRELOAD", env)
            self.assertNotIn("VK_INSTANCE_LAYERS", env)

    def test_preview_preserves_catalog_identity_and_reconnects_before_real_start(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            producer = root / "dpe-vkcapture-source"
            producer.write_text("#!/bin/sh\nexit 0\n")
            producer.chmod(0o755)
            preview = root / "dpe-vkcapture-preview"
            preview.write_text(
                "#!/usr/bin/env python3\nimport json,sys\n"
                "while True:\n"
                " print(json.dumps({'width':1,'height':1,'format':'BGRA','pixels':'AAAA/w=='}),flush=True)\n"
                " if not sys.stdin.readline(): break\n"
            )
            preview.chmod(0o755)
            instance = broker.Broker(root / "hook.sock", producer, "auto")
            self.addCleanup(instance.close)
            events = []
            instance.emit = events.append
            game = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            self.addCleanup(game.close)
            game.connect(str(root / "hook.sock"))
            instance.accept()
            token = next(iter(instance.peers))
            instance.peers[token].update(name="Game", uuid="01" * 16)
            instance.command(
                {"id": 7, "op": "preview", "token": token, "width": 320, "height": 180}
            )
            self.assertIsNone(instance.child)
            self.assertIsNone(instance.peers[token]["socket"])
            instance.command({"id": 8, "op": "list"})
            self.assertEqual(events[-1]["sources"][0]["token"], token)
            self.assertTrue(events[-1]["sources"][0]["available"])
            import select

            process = instance.previews[token]["process"]
            self.assertTrue(select.select([process.stdout], [], [], 3)[0])
            instance.preview_input(token)
            self.assertEqual(
                events[-1],
                {
                    "id": 7,
                    "ok": True,
                    "width": 1,
                    "height": 1,
                    "format": "BGRA",
                    "pixels": "AAAA/w==",
                },
            )
            instance.command(
                {"id": 10, "op": "preview", "token": token, "width": 320, "height": 180}
            )
            self.assertIs(instance.previews[token]["process"], process)
            with self.assertRaises(ValueError):
                instance.command(
                    {"id": 11, "op": "preview", "token": token, "width": 320, "height": 180}
                )
            self.assertTrue(select.select([process.stdout], [], [], 3)[0])
            instance.preview_input(token)
            self.assertEqual(events[-1]["id"], 10)
            instance.command({"id": 9, "op": "start", "token": token, "fps": 60})
            self.assertEqual(instance.previews, {})
            self.assertIsNotNone(process.poll())
            self.assertIsNone(instance.child)
            self.assertEqual(instance.pending_start["request"]["token"], token)
            reconnect = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            self.addCleanup(reconnect.close)
            reconnect.connect(str(root / "hook.sock"))
            instance.accept()
            self.assertEqual(list(instance.peers), [token])
            self.assertIsNotNone(instance.peers[token]["socket"])

    def test_stable_game_command_follows_latest_registration_and_preserves_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = {"XDG_DATA_HOME": str(root / "data"), "PATH": "/usr/bin"}
            with mock.patch.object(Path, "home", return_value=root):
                for version in ("old", "new"):
                    app = root / version
                    helper = app / "capture/vkcapture/gamecapture-launch.py"
                    helper.parent.mkdir(parents=True)
                    helper.write_text("# fixture\n")
                    self.assertTrue(command.register(app, environment))
                record = json.loads(command.state_path(environment).read_text())
                self.assertEqual(record["app"], str(root / "new"))
                stable = root / ".local/bin/dpe-game-capture"
                self.assertIn(command.MARKER, stable.read_text())
                self.assertEqual(stable.stat().st_mode & 0o777, 0o755)
                with mock.patch.object(command.os, "execvpe", side_effect=SystemExit) as execute:
                    with self.assertRaises(SystemExit):
                        command.launch(["--", "game name", "argument with spaces"], environment)
                self.assertEqual(
                    execute.call_args.args[1][-3:], ["--", "game name", "argument with spaces"]
                )
                self.assertIn(
                    "/new/capture/vkcapture/gamecapture-launch.py", execute.call_args.args[1][2]
                )

    def test_stable_game_command_keeps_unrelated_user_command_and_falls_back_to_upstream(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            helper = root / "app/capture/vkcapture/gamecapture-launch.py"
            helper.parent.mkdir(parents=True)
            helper.touch()
            stable = root / ".local/bin/dpe-game-capture"
            stable.parent.mkdir(parents=True)
            stable.write_text("my own launcher")
            with mock.patch.object(Path, "home", return_value=root):
                with self.assertRaises(ValueError):
                    command.register(root / "app", {"XDG_DATA_HOME": str(root / "data")})
            self.assertEqual(stable.read_text(), "my own launcher")
            environment = {"XDG_DATA_HOME": str(root / "missing"), "PATH": "/usr/bin"}
            with mock.patch.object(
                command.shutil, "which", return_value="/usr/bin/obs-gamecapture"
            ):
                with mock.patch.object(command.os, "execve", side_effect=SystemExit) as execute:
                    with self.assertRaises(SystemExit):
                        command.launch(["--", "game"], environment)
            self.assertEqual(execute.call_args.args[1], ["/usr/bin/obs-gamecapture", "game"])

    @unittest.skipUnless(shutil.which("cc"), "native development tools")
    def test_gl_semaphore_visibility_and_lifetime_without_gpu(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "gl-sync"
            subprocess.run(
                [
                    "cc",
                    "-std=gnu11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-O1",
                    "-g",
                    "-fsanitize=address,undefined",
                    "-fno-omit-frame-pointer",
                    str(ROOT / "tests/screenshare/vkcapture-gl-sync-check.c"),
                    "-o",
                    str(executable),
                ],
                check=True,
            )
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("real completion backpressure", result.stdout)

    @unittest.skipUnless(
        shutil.which("cc") and shutil.which("pkg-config"), "native development tools"
    )
    def test_socket_ownership_and_gpu_source_build_without_gpu(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            build(output)
            executable = output / "handoff"
            subprocess.run(
                [
                    "cc",
                    "-std=gnu11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-O1",
                    "-g",
                    "-fsanitize=address,undefined",
                    "-fno-omit-frame-pointer",
                    "-Dclock_gettime=fixture_clock_gettime",
                    str(ROOT / "tests/screenshare/vkcapture-handoff-check.c"),
                    str(ROOT / "integration/video/vkcapture-hook.c"),
                    "-o",
                    str(executable),
                ],
                check=True,
            )
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("stale ACK rejection", result.stdout)

    @unittest.skipUnless(
        shutil.which("cc") and shutil.which("pkg-config"), "native development tools"
    )
    def test_source_reconfiguration_keeps_gpu_lifetimes(self):
        flags = subprocess.check_output(
            ["pkg-config", "--cflags", "--libs", "libpipewire-0.3", "libdrm"], text=True
        ).split()
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "source-state"
            subprocess.run(
                [
                    "cc",
                    "-std=gnu11",
                    "-O1",
                    "-g",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-ffunction-sections",
                    "-Wl,--gc-sections",
                    "-fsanitize=address,undefined",
                    "-fno-omit-frame-pointer",
                    str(ROOT / "tests/screenshare/vkcapture-source-check.c"),
                    str(ROOT / "integration/video/vkcapture-relay.c"),
                    *flags,
                    "-o",
                    str(executable),
                ],
                check=True,
            )
            result = subprocess.run([str(executable)], text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("retired GPU allocation", result.stdout)

    @unittest.skipUnless(
        shutil.which("cc") and shutil.which("pkg-config"), "native development tools"
    )
    def test_relay_ack_waits_for_consumer_gpu_release(self):
        flags = subprocess.check_output(
            ["pkg-config", "--cflags", "--libs", "libpipewire-0.3", "libdrm"], text=True
        ).split()
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "relay-state"
            subprocess.run(
                [
                    "cc",
                    "-std=gnu11",
                    "-O1",
                    "-g",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-ffunction-sections",
                    "-Wl,--gc-sections",
                    "-fsanitize=address,undefined",
                    "-fno-omit-frame-pointer",
                    str(ROOT / "tests/screenshare/vkcapture-relay-check.c"),
                    *flags,
                    "-o",
                    str(executable),
                ],
                check=True,
            )
            result = subprocess.run([str(executable)], text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("GPU release gates ACK", result.stdout)

    def test_hello_rejects_truncation_and_descriptors(self):
        import array

        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        packet = (
            struct.pack("=IHH", broker.MAGIC, broker.VERSION, 1)
            + bytes(104)
            + b"Game\0"
            + bytes(11)
        )
        a.send(packet)
        old = broker.hello(b)
        self.assertEqual(old["name"], "Game")
        self.assertFalse(old["direct"])
        capable = bytearray(packet)
        struct.pack_into("=I", capable, 44, 8)
        a.send(capable)
        self.assertTrue(broker.hello(b)["direct"])
        a.send(packet + b"x")
        with self.assertRaises(ValueError):
            broker.hello(b)
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            a.sendmsg([packet], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [fd]))])
            with self.assertRaises(ValueError):
                broker.hello(b)
        finally:
            os.close(fd)

    def test_launch_preserves_existing_hooks_without_global_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "layers").mkdir()
            (root / "backend.json").write_text('{"protocol":1}')
            for name in (
                "layers/dpe_vkcapture.json",
                "libobs_glcapture.so",
                "libVkLayer_obs_vkcapture.so",
            ):
                (root / name).touch()
            original = {
                "LD_PRELOAD": "existing.so",
                "VK_INSTANCE_LAYERS": "existing_layer",
                "VK_ADD_LAYER_PATH": "/existing",
                "PRESSURE_VESSEL_FILESYSTEMS_RO": "/existing-read-only",
            }
            result = launcher.launch_environment(root, "/run/user/123/dpe/hook.sock", original)
            self.assertEqual(result["LD_PRELOAD"], f"existing.so:{root}/libobs_glcapture.so")
            self.assertTrue(result["VK_INSTANCE_LAYERS"].endswith(":existing_layer"))
            self.assertTrue(result["VK_ADD_LAYER_PATH"].endswith(":/existing"))
            self.assertEqual(
                result["PRESSURE_VESSEL_FILESYSTEMS_RO"],
                f"/existing-read-only:{root}:/run/user/123/dpe",
            )
            self.assertNotIn("PRESSURE_VESSEL_FILESYSTEMS_RW", result)
            self.assertNotIn("OBS_VKCAPTURE", result)
            self.assertNotIn("DPE_VKCAPTURE_SOCKET", original)
            vulkan = launcher.launch_environment(
                root, "/run/user/123/dpe/hook.sock", {**original, "OBS_VKCAPTURE": "1"}
            )
            self.assertEqual(vulkan["LD_PRELOAD"], "existing.so")
            self.assertNotIn("OBS_VKCAPTURE", vulkan)
            self.assertTrue(vulkan["VK_INSTANCE_LAYERS"].startswith("VK_LAYER_DPE_"))
            self.assertIn("DPE_VKCAPTURE_SOCKET", vulkan)

    def test_launcher_creates_only_private_socket_directory_before_container_start(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            endpoint = root / "private/hook.sock"
            launcher.prepare_socket_directory(str(endpoint))
            self.assertEqual(endpoint.parent.stat().st_mode & 0o777, 0o700)
            self.assertFalse(endpoint.exists())
            launcher.prepare_socket_directory(str(endpoint))
            endpoint.parent.chmod(0o755)
            with self.assertRaisesRegex(ValueError, "private runtime directory"):
                launcher.prepare_socket_directory(str(endpoint))
            link = root / "link"
            link.symlink_to(endpoint.parent, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "private runtime directory"):
                launcher.prepare_socket_directory(str(link / "hook.sock"))

    def test_broker_selection_rate_and_child_exit_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            producer = root / "producer"
            producer.write_text(
                "#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps({'event':'ready','nodeId':9,'fps':30}),flush=True)\nline=sys.stdin.readline()\nassert line=='rate 15\\n',repr(line)\nprint(json.dumps({'event':'ended','failed':False,'frames':7}),flush=True)\n"
            )
            producer.chmod(0o755)
            b = broker.Broker(root / "hook.sock", producer, "auto")
            self.addCleanup(b.close)
            events = []
            b.emit = events.append
            peer = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            self.addCleanup(peer.close)
            peer.connect(str(root / "hook.sock"))
            b.accept()
            token = next(iter(b.peers))
            record = b.peers[token]
            record.update(name="Owned game", uuid="01" * 16)
            b.command({"id": 1, "op": "list"})
            self.assertIsNone(b.child)
            self.assertNotIn("pid", events[-1]["sources"][0])
            b.command({"id": 2, "op": "start", "token": token, "fps": 30})
            child = b.child
            with self.assertRaises(ValueError):
                b.command({"op": "rate", "fps": True})
            b.command({"id": 3, "op": "rate", "fps": 15})
            self.assertEqual(events[-1]["requestedFps"], 15)
            child.wait(timeout=3)
            for _ in range(4):
                if child.stdout.closed:
                    break
                b.producer_input()
            self.assertTrue(child.stdout.closed)
            self.assertTrue(any(item.get("event") == "ended" for item in events))
            self.assertEqual(child.returncode, 0)
            self.assertFalse(any(key.data == "producer" for key in b.selector.get_map().values()))

    def test_broker_never_replaces_existing_socket(self):
        with tempfile.TemporaryDirectory() as directory:
            endpoint = Path(directory) / "hook.sock"
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            sock.bind(str(endpoint))
            self.addCleanup(sock.close)
            with self.assertRaises(ValueError):
                broker.Broker(endpoint, "/bin/true", "/dev/dri/renderD128")


if __name__ == "__main__":
    unittest.main()
