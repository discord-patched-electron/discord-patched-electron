"""Check locked client modules against their bundled or one custom runtime."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess

from check_krisp_audio import analyze, generate
from common import ROOT, download, read_json, write_json
from patch_krisp import patch
from probe_runtime import probe


def check(output: Path, custom_runtime: Path | None = None):
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use an empty runtime check directory")
    output.mkdir(parents=True, exist_ok=True)
    fixture = output / "audio-fixture"
    generate(fixture)
    results = []
    clients = read_json(ROOT / "config/clients.lock.json")["clients"]
    for channel, client in clients.items():
        directory = output / channel
        directory.mkdir()
        for name in ("core", "discord_voice", "discord_krisp"):
            source = client["sources"][name]
            archive = download(source["url"], directory / (name + ".tar.br"), source["sha256"])
            target = directory / "core" if name == "core" else directory / "modules" / name
            target.mkdir(parents=True)
            subprocess.run(["tar", "-xf", str(archive), "--use-compress-program=brotli",
                "--strip-components=1", "-C", str(target), "files/"], check=True)
        info = read_json(directory / "core/resources/build_info.json")
        if info["releaseChannel"] != channel or info["version"] != client["version"]:
            raise ValueError("Extracted client identity differs from the lock")
        krisp = directory / "modules/discord_krisp"
        patched = patch(krisp / "discord_krisp.node")
        (krisp / "KMS/logs").mkdir(parents=True, exist_ok=True)
        write_json(directory / "krisp-patch.json", patched)
        runtime = custom_runtime or directory / "core"
        executable = "electron" if custom_runtime else {
            "stable": "Discord", "ptb": "DiscordPTB", "canary": "DiscordCanary"}[channel]
        result = probe(runtime, executable, directory / "modules", directory / "probe", fixture / "input")
        if not result["passed"]:
            raise ValueError(f"{channel}: native runtime fixture failed; inspect runtime-report.json")
        audio = analyze(fixture, directory / "probe")
        write_json(directory / "audio-report.json", audio)
        if not audio["offline_krisp_verified"]:
            raise ValueError(f"{channel}: offline Krisp check failed; inspect audio-report.json")
        results.append({"channel": channel, "client_version": client["version"],
            "runtime_versions": result["versions"], "original_inputs": result["original_inputs"],
            "native_fixture_passed": result["passed"], "offline_krisp_verified": audio["offline_krisp_verified"],
            "noise_reduction_db": audio["noise_reduction_db"]})
        print(f"{channel}: native bridge and offline Krisp passed", flush=True)
    write_json(output / "runtime-checks.json", {"schema": 1,
        "runtime_kind": "custom" if custom_runtime else "official bundled baseline",
        "combinations": results, "real_discord_call_verified": False,
        "per_app_audio_verified": False, "publishable": False})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, help="Extracted custom Electron distribution; defaults to each bundled runtime")
    args = parser.parse_args()
    check(args.output, args.runtime.resolve() if args.runtime else None)


if __name__ == "__main__":
    main()
