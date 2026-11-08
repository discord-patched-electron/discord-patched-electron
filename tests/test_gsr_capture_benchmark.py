import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from benchmark_gsr_capture import bus_config, recorder_command

ROOT = Path(__file__).resolve().parents[1]


class GsrCaptureBenchmark(unittest.TestCase):
    def test_comparison_changes_only_the_capture_source(self):
        args = argparse.Namespace(
            recorder=Path("/usr/bin/gpu-screen-recorder"), monitor="DP-2", method="kms"
        )
        kms = recorder_command(args)
        args.method = "portal"
        portal = recorder_command(args)
        self.assertEqual(
            [(a, b) for a, b in zip(kms, portal, strict=True) if a != b], [("DP-2", "portal")]
        )
        self.assertEqual(kms[kms.index("-o") + 1], "/dev/null")
        self.assertEqual(kms[kms.index("-fallback-cpu-encoding") + 1], "no")
        self.assertEqual(kms[kms.index("-restore-portal-session") + 1], "no")
        self.assertNotIn("-a", kms)

    def test_private_bus_cannot_activate_an_external_portal(self):
        root = ElementTree.fromstring(bus_config(Path("/tmp/owned&private/bus")))
        self.assertEqual(root.find("listen").text, "unix:path=/tmp/owned&private/bus")
        self.assertEqual(root.find("auth").text, "EXTERNAL")
        self.assertFalse(root.findall(".//servicedir"))
        self.assertFalse(root.findall(".//standard_session_servicedirs"))
        self.assertFalse(root.findall(".//include"))
        self.assertFalse(root.findall(".//includedir"))

    def test_full_portal_handshake_without_a_broadcast_match(self):
        if not all(shutil.which(name) for name in ("bwrap", "dbus-daemon")):
            self.skipTest("Private bus and user namespace tools required")
        imports = subprocess.run(
            ["/usr/bin/python3", "-c", "import dbus,gi"], capture_output=True, check=False
        )
        if imports.returncode:
            self.skipTest("System dbus-python/PyGObject required")
        prefix = [
            "bwrap",
            "--die-with-parent",
            "--unshare-user",
            "--unshare-net",
            "--ro-bind",
            "/",
            "/",
        ]
        probe = subprocess.run([*prefix, "--", "/usr/bin/true"], capture_output=True, check=False)
        if probe.returncode:
            self.skipTest("Unprivileged user/network namespaces unavailable")
        with tempfile.TemporaryDirectory(prefix="dpe-portal-protocol-") as folder:
            root = Path(folder)
            children, logs = [], []
            environment = dict(
                os.environ,
                DBUS_SESSION_BUS_ADDRESS=f"unix:path={root}/bus",
                XDG_RUNTIME_DIR=str(root),
                PIPEWIRE_REMOTE="dpe-capture-test",
                DPE_SCREENSHARE_OUTPUT=str(root),
            )
            (root / "config.json").write_text(
                json.dumps({"source": "screen", "width": 1920, "height": 1080})
            )
            (root / "source.log").write_text("123\n")
            (root / "bus.conf").write_text(bus_config(root / "bus"))

            def start(command, name):
                log = (root / name).open("w")
                logs.append(log)
                children.append(subprocess.Popen(command, env=environment, stdout=log, stderr=log))

            def ready(path):
                until = time.monotonic() + 5
                while not path.exists():
                    if time.monotonic() > until or any(p.poll() is not None for p in children):
                        self.fail({file.name: file.read_text() for file in root.glob("*.log")})
                    time.sleep(0.02)

            with socket.socket(socket.AF_UNIX) as listener:
                listener.bind(str(root / "dpe-capture-test"))
                listener.listen()
                try:
                    start(
                        [
                            "dbus-daemon",
                            "--nofork",
                            "--nopidfile",
                            "--config-file",
                            str(root / "bus.conf"),
                        ],
                        "dbus.log",
                    )
                    ready(root / "bus")
                    start(
                        [
                            *prefix,
                            "--bind",
                            str(root),
                            str(root),
                            "--",
                            "/usr/bin/python3",
                            str(ROOT / "tests/screenshare/portal.py"),
                        ],
                        "portal.log",
                    )
                    ready(root / "portal-ready")
                    result = subprocess.run(
                        [
                            "/usr/bin/python3",
                            str(ROOT / "tests/screenshare/portal-protocol-client.py"),
                        ],
                        env=environment,
                        capture_output=True,
                        text=True,
                        timeout=8,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("directed portal handshake passed", result.stdout)
                finally:
                    for child in reversed(children):
                        child.terminate()
                    for child in reversed(children):
                        try:
                            child.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            child.kill()
                            child.wait()
                    for log in logs:
                        log.close()


if __name__ == "__main__":
    unittest.main()
