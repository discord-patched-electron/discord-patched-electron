"""Run a synthetic native bridge/module probe in a copied, isolated distribution.

The copied app.asar is replaced with the local fixture. Never invoke an installed
client or rely on ELECTRON_RUN_AS_NODE, which Discord disables in bundled builds.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import shutil
import subprocess

from common import ROOT, download, hash_files, read_json, run, sha256, write_json


def probe(runtime: Path, executable: str, modules: Path, output: Path, audio_fixtures: Path | None = None) -> dict:
    runtime, modules, output = runtime.resolve(), modules.resolve(), output.absolute()
    if executable not in {"electron", "Discord", "DiscordPTB", "DiscordCanary"}:
        raise ValueError("Unsupported runtime executable name")
    if output.is_relative_to(runtime) or output.is_relative_to(modules):
        raise ValueError("Output must be outside the input distribution and modules")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use an empty probe output directory")
    originals = {"executable": sha256(runtime / executable),
        "voice": sha256(modules / "discord_voice/discord_voice.node"),
        "krisp": sha256(modules / "discord_krisp/discord_krisp.node")}
    output.mkdir(parents=True, exist_ok=True)
    for name in ("runtime", "modules", "home", "profile", "run", "cache", "data", "config", "headers"):
        (output / name).mkdir()
    (output / "run").chmod(0o700)
    run("cp", "-a", "--reflink=auto", str(runtime) + "/.", output / "runtime")
    run("cp", "-a", "--reflink=auto", str(modules) + "/.", output / "modules")
    fixture = ROOT / "tests/runtime"
    header = read_json(fixture / "header.lock.json")
    download(header["url"], output / "headers/electron_video_shared.h", header["sha256"])
    run("g++", "-std=c++17", "-shared", "-fPIC", "-Wno-attributes", "-DNAPI_VERSION=8",
        "-I/usr/include/node", "-I" + str(output / "headers"), fixture / "frame_addon.cc",
        "-ldl", "-o", output / "frame_addon.node")
    # This only changes our freshly copied distribution, including for a bundled
    # Discord executable. Its ordinary bootstrap/profile is never run.
    run(ROOT / "node_modules/.bin/asar", "pack", fixture, output / "runtime/resources/app.asar")
    environment = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8",
        "HOME": str(output / "home"), "XDG_CONFIG_HOME": str(output / "config"),
        "XDG_CACHE_HOME": str(output / "cache"), "XDG_DATA_HOME": str(output / "data"),
        "XDG_RUNTIME_DIR": str(output / "run"), "DISCORD_USER_DATA_DIR": str(output / "profile"),
        "DPE_PROBE_PROFILE": str(output / "profile"), "DPE_PROBE_REPORT": str(output / "runtime-report.json"),
        "DPE_PROBE_MODULES": str(output / "modules"), "DPE_PROBE_ADDON": str(output / "frame_addon.node"),
        "PULSE_SERVER": "unix:" + str(output / "run/no-pulse"), "PIPEWIRE_REMOTE": "no-pipewire",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=" + str(output / "run/no-dbus")}
    if audio_fixtures is not None:
        audio = output / "audio-input"
        audio.mkdir()
        files = sorted(audio_fixtures.glob("*.wav"))
        if not files:
            raise ValueError("Audio fixture directory contains no WAV files")
        for file in files:
            shutil.copy2(file, audio / file.name)
        environment["DPE_PROBE_AUDIO_INPUT"] = str(audio)
    command = ["xvfb-run", "-a", "-s", "-screen 0 1024x768x24 -nolisten tcp",
        str(output / "runtime" / executable), "--ozone-platform=x11", "--disable-gpu",
        "--disable-setuid-sandbox"]
    timed_out = False
    with (output / "runtime.log").open("w") as log:
        process = subprocess.Popen(command, env=environment, cwd=output, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:
            process.wait(timeout=100 if audio_fixtures is not None else 40)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
    report_path = output / "runtime-report.json"
    report = read_json(report_path) if report_path.exists() else {"passed": False, "error": "Fixture did not produce a report"}
    report.update(process_exit=process.returncode, timed_out=timed_out, original_inputs=originals,
        public_header=header, frame_addon_sha256=sha256(output / "frame_addon.node"),
        fixture_inputs=hash_files(["scripts/probe_runtime.py", *[str(p.relative_to(ROOT)) for p in fixture.iterdir() if p.is_file()]]),
        publishable=False)
    report["passed"] = (report.get("passed") is True and process.returncode == 0 and not timed_out
                        and report.get("fixture") == "discord-native-bridge"
                        and report.get("userData") == str(output / "profile"))
    write_json(report_path, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runtime", type=Path)
    parser.add_argument("--executable", default="electron")
    parser.add_argument("--modules", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audio-fixtures", type=Path, help="Optional synthetic WAV directory for offline voice processing")
    args = parser.parse_args()
    report = probe(args.runtime, args.executable, args.modules, args.output, args.audio_fixtures)
    print(f"Native fixture passed: {report['passed']}; real call/audio validation remains separate")
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
