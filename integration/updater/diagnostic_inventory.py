"""Read-only diagnostic state and system inventory; never activate updates or media."""

import json
import os
import sqlite3
import tempfile
from pathlib import Path

MAX_STATE = 1024 * 1024


def state_inventory(profile, app=None):
    result = {"state": {}, "unavailable": []}

    def read(file, label):
        try:
            if file.is_symlink() or file.stat().st_size > MAX_STATE:
                raise ValueError("State file is linked or exceeds size limit")
            value = json.loads(file.read_text())
            result["state"][label] = value
            return value
        except (OSError, ValueError, RecursionError) as error:
            result["unavailable"].append({"source": label, "reason": str(error)})
            return None

    for relative in (
        "dpe-updater/current.json",
        "dpe-updater/activation.json",
        "dpe-updater/mods.json",
        "dpe-updater/approvals/current.json",
        "dpe-updater/approvals/last-check.json",
    ):
        value = read(profile / relative, relative)
        if (
            relative.endswith("/current.json")
            and isinstance(value, str)
            and len(value) == 64
            and all(char in "0123456789abcdef" for char in value)
        ):
            read(profile / "dpe-updater/generations" / value / "client.json", "client-generation")
    for folder, filename in (("dpe-application", "state.json"), ("dpe-runtime", "current.json")):
        parent = profile / folder
        if parent.is_dir() and not parent.is_symlink():
            for directory in sorted(parent.iterdir())[:16]:
                if not directory.is_dir() or directory.is_symlink():
                    continue
                value = read(directory / filename, f"{folder}/{directory.name}/{filename}")
                read(directory / "last-check.json", f"{folder}/{directory.name}/last-check.json")
                if (
                    folder == "dpe-runtime"
                    and isinstance(value, str)
                    and len(value) == 64
                    and all(char in "0123456789abcdef" for char in value)
                ):
                    read(
                        directory / "generations" / value / "runtime.json",
                        f"runtime/{directory.name}",
                    )
    if app:
        if (app.parent / "application.json").is_file():
            read(app.parent / "application.json", "selected-application")
            read(app.parent / "last-check.json", "application-last-check")
        result["helper_files"] = {}
        for relative in (
            "launch.sh",
            "integration/updater/entry.cjs",
            "integration/updater/check.py",
            "integration/updater/bootstrap.py",
            "integration/updater/export-logs.cjs",
            "integration/updater/export_logs.py",
            "integration/updater/log_redaction.py",
            "integration/updater/diagnostic_inventory.py",
            "integration/updater/diagnostic_log.py",
            "integration/updater/diagnostic-log.cjs",
            "integration/audio/app-audio.cjs",
            "integration/camera/flip.cjs",
            "capture/dpe-wayland-source",
            "capture/dpe-kms-source",
            "capture/dpe-kms-server",
            "capture/vkcapture/dpe-vkcapture-source",
        ):
            file = app / relative
            try:
                result["helper_files"][relative] = {
                    "present": file.is_file(),
                    "bytes": file.stat().st_size,
                    "readable": os.access(file, os.R_OK),
                    "executable": os.access(file, os.X_OK),
                }
            except OSError:
                result["helper_files"][relative] = {"present": False}
        for filename in (
            "local-build.json",
            "runtime-update.json",
            "client-distribution.json",
            "resources/build_info.json",
            "dev-version.json",
        ):
            value = read(app / filename, f"application/{filename}")
            # Large compiler command inventories are unnecessary in a desktop bug report.
            if filename == "local-build.json" and isinstance(value, dict):
                result["state"][f"application/{filename}"] = {
                    key: value[key]
                    for key in (
                        "source_commit",
                        "source_dirty",
                        "electron_version",
                        "runtime_key",
                        "executable_sha256",
                        "client_version",
                        "channel",
                    )
                    if key in value
                }
    database = profile / "installer.db"
    if database.is_file() and not database.is_symlink():
        try:
            with sqlite3.connect(
                database.as_uri() + "?mode=ro", uri=True, timeout=0.5
            ) as connection:
                # Only DPE activation and host records; never export the database or install_id.
                rows = connection.execute(
                    "SELECT key, value FROM key_values WHERE key LIKE 'dpe/active/%' OR key LIKE 'host/app/%' LIMIT 32"
                ).fetchall()
                result["state"]["installer-records"] = {
                    key: json.loads(value) for key, value in rows if len(value) <= MAX_STATE
                }
        except (sqlite3.Error, ValueError) as error:
            result["unavailable"].append({"source": "installer-records", "reason": str(error)})
    result["storage"] = {}
    for label, directory in (("profile", profile), ("temporary", Path(tempfile.gettempdir()))):
        try:
            info = os.statvfs(directory)
            result["storage"][label] = {
                "available_bytes": info.f_bavail * info.f_frsize,
                "free_inodes": info.f_favail,
                "writable": os.access(directory, os.W_OK),
            }
        except OSError as error:
            result["unavailable"].append({"source": label, "reason": str(error)})
    return result


def hardware_inventory():
    result = {"session": {}, "graphics": [], "camera_nodes": []}
    for key in (
        "XDG_SESSION_TYPE",
        "XDG_CURRENT_DESKTOP",
        "DESKTOP_SESSION",
        "DPE_CLIENT_OFFLINE",
        "DPE_APPLICATION_UPDATES",
        "DPE_CLIENT_UPDATES",
        "DPE_RUNTIME_UPDATES",
        "DPE_RUNTIME_BUNDLED",
        "DPE_CLIENT_PINNED",
        "DPE_IGNORE_FLAGS",
        "DPE_CAMERA_FAST_PROBE",
        "DPE_APP_AUDIO_TRACE",
        "DPE_APP_AUDIO_DEBUG",
    ):
        if key in os.environ:
            result["session"][key] = os.environ[key][:256]
    for device in sorted(Path("/sys/class/drm").glob("renderD*"))[:16]:
        entry = {
            "node": f"/dev/dri/{device.name}",
            "readable": os.access(f"/dev/dri/{device.name}", os.R_OK),
            "writable": os.access(f"/dev/dri/{device.name}", os.W_OK),
        }
        for field in ("vendor", "device"):
            try:
                entry[field] = (device / "device" / field).read_text().strip()[:32]
            except OSError:
                pass
        try:
            entry["driver"] = (device / "device/driver").resolve(strict=True).name
        except OSError:
            pass
        result["graphics"].append(entry)
    # Inventory permissions only. Do not open a camera, read frames or enumerate device serials.
    for device in sorted(Path("/sys/class/video4linux").glob("video*"))[:32]:
        node = f"/dev/{device.name}"
        result["camera_nodes"].append(
            {
                "node": node,
                "readable": os.access(node, os.R_OK),
                "writable": os.access(node, os.W_OK),
            }
        )
    return result


def system_queries():
    units = [
        "pipewire.service",
        "pipewire-pulse.service",
        "pulseaudio.service",
        "wireplumber.service",
        "xdg-desktop-portal.service",
        "xdg-desktop-portal-gtk.service",
        "xdg-desktop-portal-kde.service",
        "xdg-desktop-portal-gnome.service",
        "xdg-desktop-portal-hyprland.service",
        "xdg-desktop-portal-wlr.service",
    ]
    return {
        "services.log": [
            "systemctl",
            "--user",
            "--no-pager",
            "show",
            "--property=Id,LoadState,ActiveState,SubState,Result,ExecMainCode,ExecMainStatus,NRestarts",
            *units,
        ],
        "media-services.log": [
            "journalctl",
            "--user",
            "--no-pager",
            "--since=-7days",
            "-n",
            "3000",
            "-o",
            "short-iso",
            *[arg for unit in units for arg in ("-u", unit)],
        ],
        "audio-server.log": ["pactl", "--version"],
        "graphics-kernel.log": [
            "journalctl",
            "--no-pager",
            "-k",
            "--since=-7days",
            "-n",
            "500",
            "-g",
            "drm|amdgpu|i915|NVRM|nouveau|GPU HANG",
        ],
        "graphics.log": ["lspci", "-nnk", "-d", "::03xx"],
    }
