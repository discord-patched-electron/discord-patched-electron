"""Content identities shared across Discord clients; packaging changes avoid rebuilds."""

from common import digest, hash_files, package_name
from runtime_fingerprints import runtime_key


def integration_key(
    runtime: str, client: dict, electron_channel: str, camera: dict | None = None
) -> str:
    return digest(
        {
            "runtime": runtime,
            "client": client,
            "camera_support": camera,
            "package": package_name(client["channel"], electron_channel),
            "inputs": hash_files(
                [
                    "scripts/prepare_app.py",
                    "scripts/stage_updater.py",
                    "scripts/client_native.py",
                    "integration/updater/client.py",
                    "integration/updater/runtime.py",
                    "integration/updater/check.py",
                    "integration/updater/entry.cjs",
                    "integration/updater/module-updater.cjs",
                    "integration/updater/patches.cjs",
                    "integration/updater/desktop-exec.cjs",
                    "config/openasar.lock.json",
                    "scripts/patch_krisp.py",
                    "scripts/desktop_identity.py",
                    "scripts/patch_app_audio.py",
                    "integration/audio/app-audio.cjs",
                    "scripts/patch_desktop_core.py",
                    "integration/desktop/safe-storage.cjs",
                    "integration/audio/voice-devices.cjs",
                    "integration/audio/window_inventory.py",
                    "integration/audio/wayland-audio.cjs",
                    "integration/audio/pipewire_audio.py",
                    "integration/camera/v4l2-probe.c",
                    "scripts/camera_scope.py",
                    "scripts/build_camera_support.py",
                    "packaging/upstream/krisp-patcher.py",
                    "pyproject.toml",
                    "uv.lock",
                    "integration/launcher/discord-launcher.sh",
                    "bun.lock",
                    "scripts/generate_package.py",
                    "scripts/build_local_app.py",
                    "scripts/package_release.py",
                    "scripts/check_installed_package.py",
                    "scripts/check_release_packages.py",
                    "scripts/check_wayland_audio.py",
                    "scripts/smoke_packages.py",
                    "tests/wayland/preload.cjs",
                    "packaging/linux/refresh-mod-backup.sh",
                    "packaging/linux/postinstall.sh",
                    "scripts/releases.py",
                    "scripts/release_signing.py",
                    "packaging/release-signing-key.asc",
                    "scripts/publish_aur.py",
                    "scripts/channels.py",
                    "config/channels.json",
                    ".github/workflows/release.yml",
                    ".github/workflows/release-channels.yml",
                    ".github/actions/check-client-mods/action.yml",
                    "scripts/stage_package.py",
                    "packaging/PKGBUILD.in",
                    "packaging/upstream/provenance.json",
                    "THIRD_PARTY.md",
                    "LICENSE",
                ]
            ),
        }
    )
