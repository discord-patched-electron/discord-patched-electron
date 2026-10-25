"""Test real native application audio in a disposable X11/PipeWire session.

No Discord account, remote client UI, physical microphone or desktop audio is
used. This is a module-boundary test, not live-call/Wayland qualification.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import socket
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from channels import enabled_discord_channels
from client_inputs import load_clients, validate_channel
from common import ROOT, digest, download, read_json, sha256, write_json
from patch_app_audio import patch


def analyze(output: Path) -> dict:
    expected = {
        "system": (True, True),
        "app-a": (True, False),
        "app-b": (False, True),
        "disabled": (False, False),
        "unknown": (False, False),
        "re-enabled": (True, False),
        "app-exited": (False, False),
        "cleared": (False, False),
    }
    results = {}
    for name, wanted in expected.items():
        file = output / (name + ".raw")
        samples = np.fromfile(file, dtype="<i2").astype(float) if file.exists() else np.array([])
        # Fit independent tones per 10 ms callback frame. A whole-recording FFT
        # can hide short leaks and creates sidebands across callback gaps.
        width = 480
        t = np.arange(width) / 48000
        matrix = np.array(
            [fn(2 * np.pi * hz * t) for hz in (440, 880) for fn in (np.cos, np.sin)]
        ).T
        if len(samples) >= width:
            frames = samples[: len(samples) // width * width].reshape(-1, width).T
            coefficients = np.linalg.lstsq(matrix, frames, rcond=None)[0]
            residual = np.sqrt(np.mean((matrix @ coefficients - frames) ** 2, axis=0))
            levels = np.linalg.norm(coefficients.T.reshape(-1, 2, 2), axis=2)
            clean = residual < 100
            fraction = float(np.mean(clean))
            amplitudes = np.median(levels, axis=0).tolist()
            maximum = levels[clean].max(axis=0).tolist() if np.any(clean) else [32768.0] * 2
        else:
            amplitudes = maximum = [0.0, 0.0]
            fraction = 0.0
        peak = float(np.max(np.abs(samples))) if len(samples) else 0.0
        passed = (
            # RawSamples is intermittent diagnostic output, not a clocked recording.
            # Keep the original 100 ms evidence minimum (ten complete frames).
            len(samples) >= 4800
            and fraction >= 0.9
            and all(
                value > 500 if include else maximum[i] < 100
                for i, (value, include) in enumerate(zip(amplitudes, wanted, strict=True))
            )
            if any(wanted)
            else peak < 100
        )
        microphone = output / (name + "-microphone.raw")
        mic = (
            np.fromfile(microphone, dtype="<i2").astype(float)
            if microphone.exists()
            else np.array([])
        )
        mic_rms = float(np.sqrt(np.mean(mic**2))) if len(mic) else 0.0
        results[name] = {
            "passed": passed,
            "samples": len(samples),
            "peak_amplitude": peak,
            "clean_frame_fraction": fraction,
            "tone_440_amplitude": amplitudes[0],
            "tone_880_amplitude": amplitudes[1],
            "synthetic_microphone_rms": mic_rms,
        }
    return {"cases": results, "all_cases_passed": all(item["passed"] for item in results.values())}


def validate_phases(fixture, cases):
    completed = fixture.get("completed_phases", [])
    if (
        fixture.get("passed") is not True
        or not isinstance(completed, list)
        or any(not isinstance(name, str) for name in completed)
        or len(completed) != len(cases)
        or set(completed) != set(cases)
    ):
        raise ValueError("Audio fixture did not complete every required phase")


def session(output: Path):
    if {name for _, name in socket.if_nameindex()} != {"lo"}:
        raise ValueError("Audio fixture must run in a separate network namespace")
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    run = output / "run"
    run.mkdir(mode=0o700)
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "HOME": str(output / "home"),
        "XDG_RUNTIME_DIR": str(run),
        "XDG_CONFIG_HOME": str(output / "config"),
        "XDG_STATE_HOME": str(output / "state"),
        "XDG_DATA_HOME": str(output / "data"),
        "XDG_CACHE_HOME": str(output / "cache"),
        "PIPEWIRE_REMOTE": "dpe-audio",
        "PULSE_SERVER": "unix:" + str(run / "pulse/native"),
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=" + str(run / "no-dbus"),
        "DBUS_SYSTEM_BUS_ADDRESS": "unix:path=" + str(run / "no-system-dbus"),
        "DISPLAY": os.environ["DISPLAY"],
        "XAUTHORITY": os.environ.get("XAUTHORITY", ""),
        "DPE_APP_AUDIO": "1",
        "DPE_AUDIO_OUTPUT": str(output),
    }
    for name in ("home", "config", "state", "data", "cache"):
        (output / name).mkdir()
    processes = []
    logs = []

    def spawn(args, name):
        log = (output / (name + ".log")).open("w")
        logs.append(log)
        child = subprocess.Popen(args, env=env, cwd=output, stdout=log, stderr=subprocess.STDOUT)
        processes.append(child)
        return child

    def ready(test, seconds=10):
        deadline = time.monotonic() + seconds
        while not test():
            if time.monotonic() > deadline or any(child.poll() is not None for child in processes):
                raise ValueError("Fixture service did not become ready; inspect logs")
            time.sleep(0.1)

    try:
        spawn(["pipewire", "-c", str(ROOT / "tests/audio/pipewire.conf")], "pipewire")
        ready(lambda: (run / "pulse/native").exists())
        spawn(["wireplumber", "--profile=policy"], "wireplumber")
        time.sleep(1)
        inputs = {}
        for name, frequency in [("a", "440"), ("b", "880")]:
            info = output / (name + ".json")
            spawn([str(output / "tone"), frequency, str(info)], name)
            ready(info.exists)
            inputs[name] = read_json(info)
        ready(
            lambda: (
                len(
                    json.loads(
                        subprocess.check_output(
                            ["pactl", "-f", "json", "list", "sink-inputs"],
                            env=env,
                            text=True,
                            timeout=5,
                        )
                    )
                )
                == 2
            )
        )
        write_json(output / "inputs.json", inputs)
        # The network-isolated namespace maps this process to root. Only this
        # account-free fixture uses --no-sandbox; production launchers do not.
        executable = read_json(output / "identity.json")["executable"]
        child = spawn(
            [
                str(output / "runtime" / executable),
                "--no-sandbox",
                "--disable-gpu",
                "--ozone-platform=x11",
            ],
            "native",
        )
        child.wait(timeout=50)
        if child.returncode:
            raise ValueError("Native audio fixture failed; inspect native.log")
        sources = json.loads(
            subprocess.check_output(
                ["pactl", "-f", "json", "list", "source-outputs"], env=env, text=True, timeout=5
            )
        )
        if sources:
            raise ValueError("Native capture streams remain after fixture exit")
    finally:
        for child in reversed(processes):
            if child.poll() is None:
                child.terminate()
        for child in processes:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        for log in logs:
            log.close()


def check(
    output: Path,
    clients: Path,
    channel: str,
    download_only=False,
    runtime: Path | None = None,
    provenance: Path | None = None,
):
    if bool(runtime) != bool(provenance):
        raise ValueError("A custom runtime requires its verified compiler provenance")
    output = output.resolve()
    if output.exists() and any(
        p.name not in {"core.tar.br", "discord_voice.tar.br"} for p in output.iterdir()
    ):
        raise ValueError(
            "Use an empty audio directory or one containing only the locked input archives"
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
        subprocess.run(
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
        subprocess.run(
            ["cp", "-a", "--reflink=auto", str(runtime.resolve()) + "/.", str(output / "runtime")],
            check=True,
        )
    adapter_patch = patch(output / "voice", ROOT / "integration/audio/app-audio.cjs")
    identity = {
        "runtime_kind": "custom" if runtime else "bundled",
        "channel": channel,
        "client_version": client["version"],
        "client_digest": digest(client),
        "executable": executable,
        "voice_sha256": sha256(output / "voice/discord_voice.node"),
        "runtime_sha256": sha256(output / "runtime" / executable),
        "adapter_patch": adapter_patch,
    }
    write_json(output / "identity.json", identity)
    subprocess.run(
        [
            str(ROOT / "node_modules/.bin/asar"),
            "pack",
            str(ROOT / "tests/audio"),
            str(output / "runtime/resources/app.asar"),
        ],
        check=True,
    )
    flags = subprocess.check_output(
        ["pkg-config", "--cflags", "--libs", "x11", "libpulse-simple"], text=True
    ).split()
    subprocess.run(
        ["cc", str(ROOT / "tests/audio/tone.c"), "-O2", "-o", str(output / "tone"), *flags, "-lm"],
        check=True,
    )
    namespace = (
        []
        if {name for _, name in socket.if_nameindex()} == {"lo"}
        else ["unshare", "--user", "--map-root-user", "--net"]
    )
    subprocess.run(
        [
            "xvfb-run",
            "-a",
            "-s",
            "-screen 0 1024x768x24 -nolisten tcp",
            *namespace,
            sys.executable,
            str(Path(__file__).resolve()),
            "--session",
            "--output",
            str(output),
        ],
        check=True,
        timeout=80,
    )
    fixture = read_json(output / "fixture.json")
    if not fixture["passed"]:
        raise ValueError("Native audio fixture did not complete")
    if provenance:
        capacity = read_json(provenance)["capacity"]
        expected = capacity.get("compiled_version", "44.0.0-reference.1")
        if fixture["versions"]["electron"] != expected:
            raise ValueError("Audio fixture loaded a different runtime than the compiler report")
    analysis = analyze(output)
    validate_phases(fixture, analysis["cases"])
    report = {
        "schema": 1,
        "identity": identity,
        **analysis,
        "fixture": fixture,
        "x11_source_identity_verified": analysis["all_cases_passed"],
        "wayland_source_identity_verified": False,
        "real_discord_call_verified": False,
        "microphone_lifecycle_verified": False,
        "custom_runtime_verified": bool(runtime) and analysis["all_cases_passed"],
        "publishable": False,
    }
    write_json(output / "audio-report.json", report)
    if not report["all_cases_passed"]:
        raise ValueError(
            "Application isolation or lifecycle check failed; inspect audio-report.json"
        )
    print(
        channel
        + ": native application audio isolation/lifecycle passed ("
        + identity["runtime_kind"]
        + " fixture)"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--clients", type=Path, default=ROOT / "config/clients.lock.json")
    parser.add_argument("--discord", choices=enabled_discord_channels(), default="canary")
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--provenance", type=Path)
    parser.add_argument("--download-only", action="store_true")
    parser.add_argument("--session", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.session:
        session(args.output.resolve())
    else:
        check(
            args.output,
            args.clients,
            args.discord,
            args.download_only,
            args.runtime,
            args.provenance,
        )
