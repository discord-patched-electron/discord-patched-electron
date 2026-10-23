"""Real native Wayland video + private PipeWire application-audio integration test.

The test portal automates selection of disposable niri windows. The native voice
module opens the portal itself and consumes actual compositor video frames.
No host display/audio sockets or account are used by the CI container.
"""

from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import resource
import signal
import socket
import subprocess as sp
import sys
import tempfile
import time
import wave

import numpy as np
from common import ROOT, digest, download, read_json, sha256, write_json
from client_inputs import load_clients, validate_channel
from patch_app_audio import patch


def analyze(output):
    cases = {
        "app-a": (True, True, False),
        "app-b": (False, False, True),
        "system": (True, True, True),
        "disabled": (False, False, False),
        "cancelled": (False, False, False),
        "re-enabled": (True, True, False),
        "app-exited": (False, False, False),
        "cleared": (False, False, False),
    }
    results = {}
    for name, expected in cases.items():
        file = output / (name + ".raw")
        samples = (
            np.fromfile(file, dtype="<i2").astype(float)
            if file.exists()
            else np.array([])
        )
        values = []
        if len(samples) >= 4800:
            spectrum = (
                abs(np.fft.rfft(samples * np.hanning(len(samples)))) * 4 / len(samples)
            )
            frequencies = np.fft.rfftfreq(len(samples), 1 / 48000)
            values = [
                float(max(spectrum[abs(frequencies - tone) <= 20]))
                for tone in (440, 660, 880)
            ]
        else:
            values = [0.0, 0.0, 0.0]
        results[name] = {
            "samples": len(samples),
            "amplitudes": dict(zip(("440", "660", "880"), values)),
            "passed": all(
                value > 500 if wanted else value < 100
                for value, wanted in zip(values, expected)
            ),
        }
    return {
        "cases": results,
        "all_cases_passed": all(v["passed"] for v in results.values()),
    }


def session(output, pulse):
    if {name for _, name in socket.if_nameindex()} != {"lo"}:
        raise ValueError("Wayland fixture must have a private network namespace")
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    children, logs = [], []
    with tempfile.TemporaryDirectory(prefix="dpe-wl-") as temporary:
        run = Path(temporary)
        for name in ("home", "config", "state", "data", "cache"):
            (output / name).mkdir()
        env = {
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "HOME": str(output / "home"),
            "XDG_RUNTIME_DIR": str(run),
            "XDG_CONFIG_HOME": str(output / "config"),
            "XDG_STATE_HOME": str(output / "state"),
            "XDG_DATA_HOME": str(output / "data"),
            "XDG_CACHE_HOME": str(output / "cache"),
            "PIPEWIRE_REMOTE": "dpe-audio",
            "PULSE_SERVER": "unix:" + str(run / "pulse/native"),
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=" + str(run / "bus"),
            "DBUS_SYSTEM_BUS_ADDRESS": "unix:path=" + str(run / "no-system"),
            "DISPLAY": os.environ["DISPLAY"],
            "XAUTHORITY": os.environ.get("XAUTHORITY", ""),
            "LIBGL_ALWAYS_SOFTWARE": "1",
            "DPE_AUDIO_OUTPUT": str(output),
            "DPE_APP_AUDIO": "1",
            "DPE_APP_AUDIO_TRACE": "1",
        }

        def spawn(args, name, custom_env=None):
            log = (output / (name + ".log")).open("w")
            logs.append(log)
            child = sp.Popen(
                args,
                env=custom_env or env,
                stdout=log,
                stderr=sp.STDOUT,
                start_new_session=True,
            )
            children.append(child)
            return child

        def ready(test):
            deadline = time.monotonic() + 15
            while not test():
                if time.monotonic() > deadline or any(
                    p.poll() is not None for p in children
                ):
                    raise ValueError("Fixture service failed; inspect logs")
                time.sleep(0.1)

        def command(args):
            return sp.check_output(args, env=env, text=True, timeout=5)

        try:
            spawn(
                [
                    "dbus-daemon",
                    "--session",
                    "--nofork",
                    "--nopidfile",
                    "--address=" + env["DBUS_SESSION_BUS_ADDRESS"],
                ],
                "bus",
            )
            ready(lambda: (run / "bus").exists())
            config = (ROOT / "tests/audio/pipewire.conf").read_text()
            if pulse == "pulseaudio":
                config = config.replace(
                    " { name = libpipewire-module-protocol-pulse }", ""
                )
            (output / "pipewire.conf").write_text(config)
            spawn(["pipewire", "-c", str(output / "pipewire.conf")], "pipewire")
            ready(lambda: (run / "dpe-audio").exists())
            if pulse == "pulseaudio":
                (run / "pulse").mkdir()
                pa = output / "pulse.pa"
                pa.write_text(
                    f"load-module module-native-protocol-unix socket={run}/pulse/native auth-anonymous=1\n"
                    "load-module module-null-sink sink_name=test-sink rate=48000 channels=2\n"
                    "set-default-sink test-sink\nset-default-source test-sink.monitor\n"
                )
                spawn(
                    [
                        "/opt/dpe-pulse/usr/bin/pulseaudio",
                        "-nF",
                        str(pa),
                        "--daemonize=no",
                        "--exit-idle-time=-1",
                        "--use-pid-file=no",
                        "--log-target=stderr",
                        "--disable-shm=yes",
                        "--dl-search-path=/opt/dpe-pulse/usr/lib/pulseaudio/modules",
                    ],
                    "pulseaudio",
                    {
                        **env,
                        "LD_LIBRARY_PATH": "/opt/dpe-pulse/usr/lib/pulseaudio:/opt/dpe-pulse/usr/lib/pulseaudio/modules",
                    },
                )
            ready(lambda: (run / "pulse/native").exists())
            spawn(["wireplumber", "--profile=policy"], "wireplumber")
            time.sleep(0.7)
            command(["niri", "validate", "-c", str(ROOT / "tests/wayland/niri.kdl")])
            spawn(["niri", "-c", str(ROOT / "tests/wayland/niri.kdl")], "niri")
            ready(lambda: bool(list(run.glob("niri*.sock"))))
            env["NIRI_SOCKET"] = str(next(run.glob("niri*.sock")))
            env["WAYLAND_DISPLAY"] = next(
                p.name for p in run.glob("wayland-*") if p.is_socket()
            )
            env["XDG_SESSION_TYPE"] = "wayland"
            env["XDG_CURRENT_DESKTOP"] = "niri"
            env["GDK_BACKEND"] = "wayland"
            env.pop("DISPLAY")
            env.pop("XAUTHORITY")
            before = json.loads(command(["pactl", "-f", "json", "info"]))
            inputs = {}
            # A has two PulseAudio processes. B has a native PipeWire subprocess.
            for name, args in [
                ("a", ["440", "660"]),
                ("b", ["0", "0", str(output / "b.wav")]),
            ]:
                child = spawn([str(output / "tone"), *args], name)
                inputs[name] = {"pid": child.pid}
            ready(
                lambda: len(json.loads(command(["niri", "msg", "-j", "windows"]))) >= 2
            )
            write_json(output / "inputs.json", inputs)
            spawn(["python3", str(ROOT / "tests/wayland/portal.py")], "portal")
            time.sleep(0.4)
            executable = read_json(output / "identity.json")["executable"]
            child = spawn(
                [
                    str(output / "runtime" / executable),
                    "--no-sandbox",
                    "--disable-gpu",
                    "--ozone-platform=wayland",
                ],
                "native",
            )
            child.wait(timeout=100)
            if child.returncode:
                raise ValueError(
                    "Native Wayland fixture failed; inspect fixture.json/native.log"
                )
            time.sleep(1)
            after = json.loads(command(["pactl", "-f", "json", "info"]))
            defaults_unchanged = all(
                before[k] == after[k]
                for k in ("default_sink_name", "default_source_name")
            )
            objects = json.loads(command(["pw-dump"]))
            leftovers = [
                x["id"]
                for x in objects
                if str(
                    x.get("info", {}).get("props", {}).get("node.name", "")
                ).startswith("dpe-audio-")
            ]
            sources = json.loads(
                command(["pactl", "-f", "json", "list", "source-outputs"])
            )
            sinks = json.loads(command(["pactl", "-f", "json", "list", "sinks"]))
            private_sinks = [
                s["name"] for s in sinks if s["name"].startswith("dpe-audio-")
            ]
            lifecycle = {
                "default_devices_unchanged": defaults_unchanged,
                "remaining_private_nodes": leftovers,
                "remaining_capture_streams": len(sources),
                "remaining_private_sinks": private_sinks,
                "niri_version": json.loads(command(["niri", "msg", "-j", "version"])),
                "pulse_server": after["server_name"],
            }
            write_json(output / "lifecycle.json", lifecycle)
            if leftovers or sources or private_sinks or not defaults_unchanged:
                raise ValueError("Audio routing changed defaults or leaked resources")
        finally:
            for child in reversed(children):
                # A child's audio subprocess can survive the parent's exit unless
                # we also terminate its private process group.
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            for child in children:
                try:
                    child.wait(timeout=4)
                except sp.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
            for log in logs:
                log.close()


def check(
    output, clients, channel, pulse, download_only=False, runtime=None, provenance=None
):
    if bool(runtime) != bool(provenance):
        raise ValueError("Custom runtime requires verified compiler provenance")
    output = output.resolve()
    if output.exists() and any(
        p.name not in {"core.tar.br", "discord_voice.tar.br"} for p in output.iterdir()
    ):
        raise ValueError(
            "Use an empty fixture directory or one with only the locked archives"
        )
    output.mkdir(parents=True, exist_ok=True)
    client = load_clients(clients, [channel])[channel]
    validate_channel(channel, client)
    for name, directory in [("core", "runtime"), ("discord_voice", "voice")]:
        if name == "core" and runtime:
            continue
        source = client["sources"][name]
        archive = download(source["url"], output / (name + ".tar.br"), source["sha256"])
        if download_only:
            continue
        (output / directory).mkdir()
        sp.run(
            [
                "tar",
                "-xf",
                str(archive),
                "--use-compress-program=brotli",
                "--strip-components=1",
                "-C",
                str(output / directory),
                "files/",
            ],
            check=True,
        )
    if download_only:
        return
    executable = (
        "electron"
        if runtime
        else {"stable": "Discord", "ptb": "DiscordPTB", "canary": "DiscordCanary"}[
            channel
        ]
    )
    if runtime:
        (output / "runtime").mkdir()
        sp.run(
            [
                "cp",
                "-a",
                "--reflink=auto",
                str(runtime.resolve()) + "/.",
                str(output / "runtime"),
            ],
            check=True,
        )
    write_json(
        output / "identity.json",
        {
            "channel": channel,
            "client_version": client["version"],
            "client_digest": digest(client),
            "executable": executable,
            "runtime_kind": "custom" if runtime else "bundled",
            "runtime_sha256": sha256(output / "runtime" / executable),
            "voice_sha256": sha256(output / "voice/discord_voice.node"),
            "adapter_patch": patch(
                output / "voice", ROOT / "integration/audio/app-audio.cjs"
            ),
        },
    )
    sp.run(
        [
            str(ROOT / "node_modules/.bin/asar"),
            "pack",
            str(ROOT / "tests/wayland"),
            str(output / "runtime/resources/app.asar"),
        ],
        check=True,
    )
    flags = sp.check_output(
        ["pkg-config", "--cflags", "--libs", "gtk+-3.0", "libpulse-simple"], text=True
    ).split()
    sp.run(
        [
            "cc",
            str(ROOT / "tests/wayland/tone.c"),
            "-O2",
            "-o",
            str(output / "tone"),
            *flags,
            "-lm",
        ],
        check=True,
    )
    samples = (5000 * np.sin(2 * np.pi * 880 * np.arange(48000 * 100) / 48000)).astype(
        "<i2"
    )
    with wave.open(str(output / "b.wav"), "wb") as wav:
        wav.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
        wav.writeframes(np.repeat(samples, 2).tobytes())
    namespace = (
        []
        if {name for _, name in socket.if_nameindex()} == {"lo"}
        else ["unshare", "--user", "--map-root-user", "--net"]
    )
    sp.run(
        [
            "xvfb-run",
            "-a",
            "-s",
            "-screen 0 1024x768x24 -nolisten tcp",
            *namespace,
            sys.executable,
            str(Path(__file__).resolve()),
            "--session",
            "--pulse",
            pulse,
            "--output",
            str(output),
        ],
        check=True,
        timeout=130,
    )
    fixture = read_json(output / "fixture.json")
    if not fixture["passed"]:
        raise ValueError("Native Wayland fixture did not complete")
    if provenance:
        expected = read_json(provenance)["capacity"].get(
            "compiled_version", "44.0.0-reference.1"
        )
        if fixture["versions"]["electron"] != expected:
            raise ValueError("Wrong compiler runtime loaded")
    report = {
        "schema": 1,
        "identity": read_json(output / "identity.json"),
        "fixture": fixture,
        "lifecycle": read_json(output / "lifecycle.json"),
        **analyze(output),
        "wayland_source_identity_verified": True,
        "multi_process_verified": True,
        "custom_runtime_verified": bool(runtime),
        "real_discord_call_verified": False,
        "microphone_call_verified": False,
        "publishable": False,
    }
    if not report["all_cases_passed"]:
        report["wayland_source_identity_verified"] = False
        report["multi_process_verified"] = False
        report["custom_runtime_verified"] = False
    write_json(output / "audio-report.json", report)
    if not report["all_cases_passed"]:
        raise ValueError(
            "Wayland isolation/lifecycle failed; inspect audio-report.json"
        )
    print(
        f"{channel}: Wayland native video and per-application audio passed with {pulse}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--clients", type=Path, default=ROOT / "config/clients.lock.json"
    )
    parser.add_argument(
        "--discord", choices=["stable", "ptb", "canary"], default="stable"
    )
    parser.add_argument(
        "--pulse", choices=["pipewire", "pulseaudio"], default="pipewire"
    )
    parser.add_argument("--download-only", action="store_true")
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--provenance", type=Path)
    parser.add_argument("--session", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.session:
        session(args.output.resolve(), args.pulse)
    else:
        check(
            args.output,
            args.clients,
            args.discord,
            args.pulse,
            args.download_only,
            args.runtime,
            args.provenance,
        )
