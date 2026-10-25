"""Real native Wayland video + private PipeWire application-audio integration test.

The test portal automates selection of disposable niri windows. The native voice
module opens the portal itself and consumes actual compositor video frames.
No host display/audio sockets or account are used by the CI container.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import signal
import socket
import subprocess as sp
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

import numpy as np
from channels import enabled_discord_channels
from check_app_audio import validate_phases
from client_inputs import load_clients, validate_channel
from common import ROOT, digest, download, read_json, sha256, write_json
from patch_app_audio import patch


def analyze(output):
    cases = {
        "app-a": (True, True, False),
        "app-b": (False, False, True),
        "system": (True, True, True),
        "disabled": (False, False, False),
        "audio-cancelled": (False, False, False),
        "cancelled": (False, False, False),
        "re-enabled": (True, True, False),
        "app-exited": (False, False, False),
        "cleared": (False, False, False),
    }
    results = {}
    for name, expected in cases.items():
        file = output / (name + ".raw")
        samples = np.fromfile(file, dtype="<i2").astype(float) if file.exists() else np.array([])
        # RawSamples is a diagnostic callback, not a continuous clocked PCM
        # transport: callbacks at different rates can leave gaps. Concatenating
        # them into one FFT creates false sidebands at unrelated test frequencies.
        # Fit all independent tones in short contiguous frames instead. Keep the
        # worst unexpected tone in every clean frame, so brief leakage fails too.
        width = 480
        t = np.arange(width) / 48000
        matrix = np.array(
            [fn(2 * np.pi * hz * t) for hz in (440, 660, 997, 1399) for fn in (np.cos, np.sin)]
        ).T
        if len(samples) >= width:
            frames = samples[: len(samples) // width * width].reshape(-1, width).T
            coefficients = np.linalg.lstsq(matrix, frames, rcond=None)[0]
            residual = np.sqrt(np.mean((matrix @ coefficients - frames) ** 2, axis=0))
            amplitudes = np.linalg.norm(coefficients.T.reshape(-1, 4, 2), axis=2)
            clean = residual < 100
            fraction = float(np.mean(clean))
            values = np.median(amplitudes, axis=0).tolist()
            maximum = amplitudes[clean].max(axis=0).tolist() if np.any(clean) else [32768.0] * 4
        else:
            values = maximum = [0.0] * 4
            fraction = 0.0
        expected = (*expected, False)
        enough = (
            len(samples) >= 24000 and fraction >= 0.9
            if any(expected)
            else (not len(samples) or float(np.max(np.abs(samples))) < 100)
        )
        results[name] = {
            "samples": len(samples),
            "clean_frame_fraction": fraction,
            "amplitudes": dict(zip(("440", "660", "997", "1399"), values, strict=True)),
            "maximum_clean_frame_amplitudes": dict(
                zip(("440", "660", "997", "1399"), maximum, strict=True)
            ),
            "passed": enough
            and all(
                value > 500 if wanted else maximum[i] < 100
                for i, (value, wanted) in enumerate(zip(values, expected, strict=True))
            ),
        }
    return {
        "cases": results,
        "all_cases_passed": all(v["passed"] for v in results.values()),
    }


def session(output, pulse, selection):
    if {name for _, name in socket.if_nameindex()} != {"lo"}:
        raise ValueError("Wayland fixture must have a private network namespace")
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    children, logs = [], []
    selector_stop = threading.Event()
    selector_errors = []
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
                if time.monotonic() > deadline or any(p.poll() is not None for p in children):
                    raise ValueError("Fixture service failed; inspect logs")
                time.sleep(0.1)

        def command(args):
            return sp.check_output(args, env=env, text=True, timeout=5)

        def select_audio():
            """Operate only the disposable fixture's real Wayland Zenity dialog."""
            handled = set()
            try:
                while not selector_stop.wait(0.1):
                    for window in json.loads(command(["niri", "msg", "-j", "windows"])):
                        pid = window.get("pid")
                        if window.get("title") != "Screen-share audio" or pid in handled:
                            continue
                        args = Path(f"/proc/{pid}/cmdline").read_bytes().decode().split("\0")
                        if Path(args[0]).name != "zenity":
                            continue
                        rows = args[args.index("--") + 1 :]
                        choices = rows[::2]
                        selected = read_json(output / "selection.json")
                        index = 0
                        if selected["kind"] != "system":
                            # The first app's parent plays audio; B's child owns
                            # the stream. Only follow this fixture's process tree.
                            def belongs(candidate, selected_pid=selected["pid"]):
                                seen = set()
                                while candidate > 1 and candidate not in seen:
                                    if candidate == selected_pid:
                                        return True
                                    seen.add(candidate)
                                    candidate = int(
                                        Path(f"/proc/{candidate}/stat")
                                        .read_text()
                                        .rsplit(")", 1)[1]
                                        .split()[1]
                                    )
                                return False

                            index = next(
                                i
                                for i, value in enumerate(choices)
                                if value.isdecimal() and belongs(int(value))
                            )
                        command(
                            [
                                "niri",
                                "msg",
                                "action",
                                "focus-window",
                                "--id",
                                str(window["id"]),
                            ]
                        )
                        time.sleep(0.2)
                        keys = ["wtype", "-k", "Home"] + ["-k", "Down"] * index + ["-k", "Return"]
                        if selected.get("no_audio"):
                            keys = ["wtype", "-k", "Escape"]
                        command(keys)
                        # Recording waits for the actual audio selection, not
                        # just the earlier video-portal callback.
                        (output / "audio-choice-done").touch()
                        handled.add(pid)
            except Exception as error:
                selector_errors.append(str(error))

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
                config = config.replace(" { name = libpipewire-module-protocol-pulse }", "")
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
                        "--log-level=debug",
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
            # A ready socket is not a ready default recording device. Native
            # ADM initialization can hang if policy has not selected one yet.
            ready(
                lambda: any(
                    s["name"] == "test-sink.monitor"
                    for s in json.loads(command(["pactl", "-f", "json", "list", "sources"]))
                )
            )

            def select_defaults():
                return all(
                    sp.run(
                        ["pactl", action, device],
                        env=env,
                        stdout=sp.DEVNULL,
                        stderr=sp.DEVNULL,
                        timeout=5,
                    ).returncode
                    == 0
                    for action, device in [
                        ("set-default-sink", "test-sink"),
                        ("set-default-source", "test-sink.monitor"),
                    ]
                )

            ready(select_defaults)
            ready(
                lambda: (
                    json.loads(command(["pactl", "-f", "json", "info"])).get("default_source_name")
                    == "test-sink.monitor"
                )
            )
            command(["niri", "validate", "-c", str(ROOT / "tests/wayland/niri.kdl")])
            spawn(["niri", "-c", str(ROOT / "tests/wayland/niri.kdl")], "niri")
            ready(lambda: bool(list(run.glob("niri*.sock"))))
            env["NIRI_SOCKET"] = str(next(run.glob("niri*.sock")))
            env["WAYLAND_DISPLAY"] = next(p.name for p in run.glob("wayland-*") if p.is_socket())
            env["XDG_SESSION_TYPE"] = "wayland"
            env["XDG_CURRENT_DESKTOP"] = "niri"
            env["GDK_BACKEND"] = "wayland"
            env.pop("DISPLAY")
            env.pop("XAUTHORITY")
            before = json.loads(command(["pactl", "-f", "json", "info"]))
            # Seed a real private-device positive control before Discord caches
            # its initial native list. Hotplug refresh timing varies by backend.
            # The capture helper still creates its own sinks during each phase.
            control_sink = "dpe-audio-00000000000000000000000000000000-mix"
            control_module = command(
                [
                    "pactl",
                    "load-module",
                    "module-null-sink",
                    f"sink_name={control_sink}",
                    f"sink_properties=device.description={control_sink}",
                ]
            ).strip()
            inputs = {}
            # A has two PulseAudio processes. B has a native PipeWire subprocess.
            for name, args in [
                ("a", ["440", "660"]),
                ("b", ["0", "0", str(output / "b.wav")]),
            ]:
                child = spawn([str(output / "tone"), *args], name)
                inputs[name] = {"pid": child.pid}
            ready(lambda: len(json.loads(command(["niri", "msg", "-j", "windows"]))) >= 2)
            write_json(output / "inputs.json", inputs)
            spawn(["python3", str(ROOT / "tests/wayland/portal.py")], "portal")
            time.sleep(0.4)
            executable = read_json(output / "identity.json")["executable"]
            if selection == "manual":
                threading.Thread(target=select_audio, daemon=True).start()
            client_env = {**env, "DPE_FIXTURE_SELECTION": selection}
            if selection in {"manual", "interactive"}:
                client_env.pop("NIRI_SOCKET")
            if selection == "interactive":
                write_json(output / "operator-env.json", env)
            child = spawn(
                [
                    str(output / "runtime" / executable),
                    "--no-sandbox",
                    "--disable-gpu",
                    "--ozone-platform=wayland",
                ],
                "native",
                client_env,
            )
            child.wait(timeout=610 if selection == "interactive" else 100)
            if child.returncode:
                raise ValueError("Native Wayland fixture failed; inspect fixture.json/native.log")
            command(["pactl", "unload-module", control_module])
            if selector_errors:
                raise ValueError("Fixture audio selector failed: " + repr(selector_errors))
            # Exercise an uncatchable helper crash as well as normal teardown.
            crash_ready = output / "crash-ready.json"
            crash = spawn(
                [
                    "python3",
                    "-c",
                    "import sys,json,signal; from pathlib import Path; "
                    "sys.path.insert(0,sys.argv[1]); import pipewire_audio as a; "
                    "r=a.Route(); r.start(); "
                    "Path(sys.argv[2]).write_text(json.dumps([p.pid for p in r.children] + ([r.guard.pid] if r.guard else []))); "
                    "signal.pause()",
                    str(ROOT / "integration/audio"),
                    str(crash_ready),
                ],
                "crash-probe",
            )
            deadline = time.monotonic() + 10
            while not crash_ready.exists():
                if crash.poll() is not None or time.monotonic() > deadline:
                    raise ValueError("Crash-cleanup fixture could not start")
                time.sleep(0.1)
            worker_pids = read_json(crash_ready)
            crash.kill()
            crash.wait()
            deadline = time.monotonic() + 6
            while any(
                Path(f"/proc/{pid}/stat").exists()
                and Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
                for pid in worker_pids
            ):
                if time.monotonic() > deadline:
                    raise ValueError("Audio worker survived helper SIGKILL")
                time.sleep(0.1)
            time.sleep(1)
            after = json.loads(command(["pactl", "-f", "json", "info"]))
            defaults_unchanged = all(
                before[k] == after[k] for k in ("default_sink_name", "default_source_name")
            )
            objects = json.loads(command(["pw-dump"]))
            leftovers = [
                x["id"]
                for x in objects
                if str(x.get("info", {}).get("props", {}).get("node.name", "")).startswith(
                    "dpe-audio-"
                )
            ]
            sources = json.loads(command(["pactl", "-f", "json", "list", "source-outputs"]))
            sinks = json.loads(command(["pactl", "-f", "json", "list", "sinks"]))
            private_sinks = [s["name"] for s in sinks if s["name"].startswith("dpe-audio-")]
            lifecycle = {
                "default_devices_unchanged": defaults_unchanged,
                "remaining_private_nodes": leftovers,
                "remaining_capture_streams": len(sources),
                "remaining_private_sinks": private_sinks,
                "niri_version": json.loads(command(["niri", "msg", "-j", "version"])),
                "pulse_server": after["server_name"],
                "helper_crash_cleanup_verified": not (leftovers or private_sinks),
                "selection": selection,
            }
            write_json(output / "lifecycle.json", lifecycle)
            if leftovers or sources or private_sinks or not defaults_unchanged:
                raise ValueError("Audio routing changed defaults or leaked resources")
        finally:
            selector_stop.set()
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
    output,
    clients,
    channel,
    pulse,
    download_only=False,
    runtime=None,
    provenance=None,
    selection="auto",
):
    if bool(runtime) != bool(provenance):
        raise ValueError("Custom runtime requires verified compiler provenance")
    output = output.resolve()
    if output.exists() and any(
        p.name not in {"core.tar.br", "discord_voice.tar.br"} for p in output.iterdir()
    ):
        raise ValueError("Use an empty fixture directory or one with only the locked archives")
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
        else {"stable": "Discord", "ptb": "DiscordPTB", "canary": "DiscordCanary"}[channel]
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
    adapter_patch = patch(output / "voice", ROOT / "integration/audio/app-audio.cjs")
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
            "adapter_patch": adapter_patch,
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
    # Independent frequencies avoid confusing harmonic distortion with leakage.
    for name, frequency in [("b", 997), ("discord", 1399)]:
        samples = (5000 * np.sin(2 * np.pi * frequency * np.arange(48000 * 100) / 48000)).astype(
            "<i2"
        )
        with wave.open(str(output / (name + ".wav")), "wb") as wav:
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
            "--selection",
            selection,
            "--output",
            str(output),
        ],
        check=True,
        timeout=650 if selection == "interactive" else 130,
    )
    fixture = read_json(output / "fixture.json")
    if not fixture["passed"]:
        raise ValueError("Native Wayland fixture did not complete")
    if provenance:
        expected = read_json(provenance)["capacity"].get("compiled_version", "44.0.0-reference.1")
        if fixture["versions"]["electron"] != expected:
            raise ValueError("Wrong compiler runtime loaded")
    analysis = analyze(output)
    validate_phases(fixture, analysis["cases"])
    report = {
        "schema": 1,
        "identity": read_json(output / "identity.json"),
        "fixture": fixture,
        "lifecycle": read_json(output / "lifecycle.json"),
        **analysis,
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
        raise ValueError("Wayland isolation/lifecycle failed; inspect audio-report.json")
    print(f"{channel}: Wayland native video and per-application audio passed with {pulse}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--clients", type=Path, default=ROOT / "config/clients.lock.json")
    parser.add_argument("--discord", choices=enabled_discord_channels(), default="canary")
    parser.add_argument("--pulse", choices=["pipewire", "pulseaudio"], default="pipewire")
    parser.add_argument("--download-only", action="store_true")
    parser.add_argument("--selection", choices=["auto", "manual", "interactive"], default="auto")
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--provenance", type=Path)
    parser.add_argument("--session", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.session:
        session(args.output.resolve(), args.pulse, args.selection)
    else:
        check(
            args.output,
            args.clients,
            args.discord,
            args.pulse,
            args.download_only,
            args.runtime,
            args.provenance,
            args.selection,
        )
