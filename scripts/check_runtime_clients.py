"""Check locked client modules against their bundled or one custom runtime."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from channels import enabled_discord_channels
from check_krisp_audio import analyze, generate
from client_inputs import load_clients, validate_channel
from common import ROOT, digest, download, read_json, write_json
from patch_krisp import patch
from probe_runtime import probe


def check_channel(
    channel: str,
    client: dict,
    directory: Path,
    fixture: Path,
    custom_runtime: Path | None,
    expected_version: str | None,
) -> dict:
    validate_channel(channel, client)
    for name in ("core", "discord_voice", "discord_krisp"):
        source = client["sources"][name]
        archive = download(source["url"], directory / (name + ".tar.br"), source["sha256"])
        target = directory / "core" if name == "core" else directory / "modules" / name
        target.mkdir(parents=True)
        subprocess.run(
            [
                "tar",
                "-xf",
                str(archive),
                "--use-compress-program=brotli",
                "--strip-components=1",
                "-C",
                str(target),
                "files/",
            ],
            check=True,
        )
    info = read_json(directory / "core/resources/build_info.json")
    if info["releaseChannel"] != channel or info["version"] != client["version"]:
        raise ValueError("Extracted client identity differs from the lock")
    krisp = directory / "modules/discord_krisp"
    patched = patch(krisp / "discord_krisp.node")
    (krisp / "KMS/logs").mkdir(parents=True, exist_ok=True)
    write_json(directory / "krisp-patch.json", patched)
    runtime = custom_runtime or directory / "core"
    executable = (
        "electron"
        if custom_runtime
        else {"stable": "Discord", "ptb": "DiscordPTB", "canary": "DiscordCanary"}[channel]
    )
    result = probe(
        runtime, executable, directory / "modules", directory / "probe", fixture / "input"
    )
    if not result["passed"]:
        raise ValueError("Native runtime fixture failed; inspect runtime-report.json")
    if expected_version and result["versions"]["electron"] != expected_version:
        raise ValueError("Loaded Electron version differs from the compiler report")
    audio = analyze(fixture, directory / "probe")
    write_json(directory / "audio-report.json", audio)
    if not audio["offline_krisp_verified"]:
        raise ValueError("Offline Krisp check failed; inspect audio-report.json")
    return {
        "passed": True,
        "runtime_versions": result["versions"],
        "original_inputs": result["original_inputs"],
        "native_fixture_passed": result["passed"],
        "offline_krisp_verified": audio["offline_krisp_verified"],
        "noise_reduction_db": audio["noise_reduction_db"],
    }


def check(
    output: Path,
    custom_runtime: Path | None = None,
    provenance: Path | None = None,
    clients_file: Path = ROOT / "config/clients.lock.json",
    channels: list[str] | None = None,
) -> dict:
    expected_version = None
    if provenance:
        if not custom_runtime:
            raise ValueError("Compiler provenance requires a custom runtime")
        capacity = read_json(provenance)["capacity"]
        expected_version = capacity.get("compiled_version", "44.0.0-reference.1")
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use an empty runtime check directory")
    output.mkdir(parents=True, exist_ok=True)
    fixture = output / "audio-fixture"
    generate(fixture)
    results = []
    clients = load_clients(clients_file, channels)
    report = {
        "schema": 1,
        "runtime_kind": "custom" if custom_runtime else "official bundled baseline",
        "selected_channels": list(clients),
        "combinations": results,
        "all_selected_passed": False,
        "real_discord_call_verified": False,
        "per_app_audio_verified": False,
        "publishable": False,
    }
    for channel, client in clients.items():
        directory = output / channel
        directory.mkdir()
        try:
            result = check_channel(
                channel, client, directory, fixture, custom_runtime, expected_version
            )
        except Exception as error:
            result = {"passed": False, "error_type": type(error).__name__, "error": str(error)}
        result.update(
            channel=channel,
            client_version=client.get("version") if isinstance(client, dict) else None,
            client_digest=digest(client),
            publishable=False,
        )
        write_json(directory / "client-check.json", result)
        results.append(result)
        write_json(output / "runtime-checks.json", report)
        print(
            f"{channel}: {'native bridge and offline Krisp passed' if result['passed'] else result['error']}",
            flush=True,
        )
    report["all_selected_passed"] = all(result["passed"] for result in results)
    write_json(output / "runtime-checks.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--runtime",
        type=Path,
        help="Extracted custom Electron distribution; defaults to each bundled runtime",
    )
    parser.add_argument(
        "--provenance", type=Path, help="Verified compiler artifact provenance for version checking"
    )
    parser.add_argument(
        "--clients",
        type=Path,
        default=ROOT / "config/clients.lock.json",
        help="Exact client snapshot to validate",
    )
    parser.add_argument(
        "--discord",
        action="append",
        choices=enabled_discord_channels(),
        help="Validate only these channels; default is all three",
    )
    args = parser.parse_args()
    report = check(
        args.output,
        args.runtime.resolve() if args.runtime else None,
        args.provenance,
        args.clients,
        args.discord,
    )
    raise SystemExit(0 if report["all_selected_passed"] else 1)


if __name__ == "__main__":
    main()
