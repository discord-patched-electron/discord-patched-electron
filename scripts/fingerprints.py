"""Content identities shared across Discord clients; packaging changes avoid rebuilds."""
from common import ROOT, digest, hash_files, package_name, read_json


def runtime_key(target: dict, sources: dict, toolchain: dict) -> str:
    return digest({"target": {key: target[key] for key in ("version", "commit")},
                   "fork": sources["fork"], "toolchain": toolchain,
                   "inputs": hash_files([sources["fork"]["patch"], "scripts/assemble.py", "scripts/common.py",
                                         "scripts/build_reference.py", "config/build.json", "ci/runner/Dockerfile"])})


def integration_key(runtime: str, client: dict, electron_channel: str) -> str:
    return digest({"runtime": runtime, "client": client,
                   "package": package_name(client["channel"], electron_channel),
                   "inputs": hash_files(["scripts/prepare_app.py", "scripts/patch_krisp.py",
                                         "packaging/upstream/krisp-patcher.py", "requirements.txt",
                                         "integration/launcher/discord-launcher.sh", "package-lock.json"])})
