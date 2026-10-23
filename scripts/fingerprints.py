"""Content identities shared across Discord clients; packaging changes avoid rebuilds."""
from common import ROOT, digest, hash_files, package_name, read_json


def runtime_key(target: dict, sources: dict, toolchain: dict) -> str:
    port = sources.get("ports", {}).get(target["version"].split(".")[0])
    port_files = [port["patch"]] if port else []
    return digest({"target": {key: target[key] for key in ("version", "commit")},
                   "fork": sources["fork"], "port": port, "toolchain": toolchain,
                   "inputs": hash_files([sources["fork"]["patch"], "scripts/assemble.py", "scripts/common.py",
                                         "scripts/build_reference.py", "config/build.json", "ci/runner/Dockerfile", *port_files])})


def integration_key(runtime: str, client: dict, electron_channel: str) -> str:
    return digest({"runtime": runtime, "client": client,
                   "package": package_name(client["channel"], electron_channel),
                   "inputs": hash_files(["scripts/prepare_app.py", "scripts/patch_krisp.py",
                                         "packaging/upstream/krisp-patcher.py", "requirements.txt",
                                         "integration/launcher/discord-launcher.sh", "package-lock.json",
                                         "scripts/generate_package.py", "scripts/stage_package.py",
                                         "packaging/PKGBUILD.in", "packaging/upstream/provenance.json", "THIRD_PARTY.md"])})
