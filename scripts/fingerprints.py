"""Content identities shared across Discord clients; packaging changes avoid rebuilds."""
from common import digest, hash_files, package_name
from runtime_fingerprints import runtime_key


def integration_key(runtime: str, client: dict, electron_channel: str) -> str:
    return digest({"runtime": runtime, "client": client,
                   "package": package_name(client["channel"], electron_channel),
                   "inputs": hash_files(["scripts/prepare_app.py", "scripts/patch_krisp.py",
                                         "scripts/patch_app_audio.py", "integration/audio/app-audio.cjs",
                                         "integration/audio/wayland-audio.cjs", "integration/audio/pipewire_audio.py",
                                         "packaging/upstream/krisp-patcher.py", "requirements.txt",
                                         "integration/launcher/discord-launcher.sh", "package-lock.json",
                                         "scripts/generate_package.py", "scripts/stage_package.py",
                                         "packaging/PKGBUILD.in", "packaging/upstream/provenance.json", "THIRD_PARTY.md"])})
