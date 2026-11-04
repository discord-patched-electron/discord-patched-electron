import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_camera_support import build, node_headers
from capture_scope import scope
from common import ROOT


class CaptureSupport(unittest.TestCase):
    @unittest.skipUnless(shutil.which("cc"), "C toolchain required")
    def test_copy_pacing_without_game_gpu_or_wall_clock_waits(self):
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "rate-check"
            subprocess.run(
                [
                    "cc",
                    "-std=c11",
                    "-O1",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-fsanitize=address,undefined",
                    str(ROOT / "tests/screenshare/rate-check.c"),
                    "-o",
                    str(binary),
                ],
                check=True,
            )
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_unknown_native_is_not_modified_or_hooked(self):
        with tempfile.TemporaryDirectory() as temporary:
            module = Path(temporary) / "unknown.node"
            module.write_bytes(b"unknown upstream native module")
            self.assertFalse(scope(module)["supported"])
            self.assertEqual(module.read_bytes(), b"unknown upstream native module")

    @unittest.skipUnless(shutil.which("cc") and shutil.which("pkg-config"), "C toolchain required")
    def test_buffer_ownership_without_device_or_daemon(self):
        flags = subprocess.check_output(
            ["pkg-config", "--cflags", "libpipewire-0.3"], text=True
        ).split()
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "capture-check"
            subprocess.run(
                [
                    "cc",
                    "-std=c11",
                    "-O1",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-fsanitize=address,undefined",
                    "-fno-omit-frame-pointer",
                    "-I" + str(node_headers()),
                    *flags,
                    str(ROOT / "tests/screenshare/capture-check.c"),
                    "-ldl",
                    "-pthread",
                    "-o",
                    str(binary),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            result = subprocess.run([str(binary)], check=True, capture_output=True, text=True)
            self.assertIn("FIFO restoration", result.stdout)

    def test_fixture_markers_are_staged_with_native_receiver(self):
        package = json.loads((ROOT / "package.json").read_text())
        self.assertIn("tests/screenshare/*.test.cjs", package["scripts"]["test:js"])

    @unittest.skipUnless(shutil.which("cc") and shutil.which("pkg-config"), "C toolchain required")
    def test_synthetic_producers_compile_without_opening_any_devices(self):
        with tempfile.TemporaryDirectory() as temporary:
            for dmabuf in (False, True):
                packages = ["libpipewire-0.3"] + (
                    ["gbm", "egl", "glesv2", "libdrm"] if dmabuf else []
                )
                flags = subprocess.check_output(
                    ["pkg-config", "--cflags", "--libs", *packages], text=True
                ).split()
                sources = [str(ROOT / "tests/screenshare/pipewire-source.c")]
                if dmabuf:
                    sources += ["-DDPE_SOURCE_DMABUF", str(ROOT / "tests/screenshare/gpu-source.c")]
                subprocess.run(
                    [
                        "cc",
                        "-std=gnu11",
                        "-O2",
                        "-Wall",
                        "-Wextra",
                        "-Werror",
                        *sources,
                        *flags,
                        "-o",
                        str(Path(temporary) / "producer"),
                    ],
                    check=True,
                )
                # Parse failures must happen before opening a DRM device or
                # connecting to a PipeWire daemon, including duplicate modes.
                for args in (
                    ["--sync-timeline", "10"],
                    ["--dmabuf", "/unopened", "--sync-timeline", "101"],
                    ["--dmabuf", "/unopened", "--sync-timeline", "-1"],
                    ["--dmabuf", "/unopened", "--sync-timeline", ""],
                    ["--dmabuf", "/unopened", "--sync-timeline", "10x"],
                    ["--dmabuf", "/unopened", "--sync-timeline", "0", "--sync-timeline", "0"],
                ):
                    result = subprocess.run(
                        [str(Path(temporary) / "producer"), "1280", "720", "30", *args],
                        timeout=2,
                        capture_output=True,
                    )
                    self.assertEqual(result.returncode, 2, result.stderr)

    @unittest.skipUnless(shutil.which("cc") and shutil.which("pkg-config"), "C toolchain required")
    def test_reviewed_call_shape_and_register_return_abi(self):
        flags = subprocess.check_output(
            ["pkg-config", "--cflags", "libpipewire-0.3"], text=True
        ).split()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            build(directory / "helper")
            subprocess.run(
                [
                    "cc",
                    "-std=c11",
                    "-O1",
                    "-fPIC",
                    "-shared",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-I" + str(node_headers()),
                    *flags,
                    str(ROOT / "tests/screenshare/capture-fixture.c"),
                    "-ldl",
                    "-o",
                    str(directory / "discord_voice.node"),
                ],
                check=True,
            )
            for mode in ("observe", "latest", "paced"):
                result = subprocess.run(
                    [
                        "node",
                        str(ROOT / "tests/screenshare/capture-native-check.cjs"),
                        str(directory),
                        str(directory / "helper/libdpe-camera-probe.so"),
                        mode,
                    ],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
