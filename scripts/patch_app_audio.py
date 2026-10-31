"""Install the module adapter; reject an unknown native wrapper layout."""

import shutil
from pathlib import Path

from common import sha256
from patch_pulse_audio import patch as patch_pulse_audio

ANCHOR = "function bindConnectionInstance(instance) {\n"
INSERT = "    instance = require('./dpe-app-audio.cjs').wrap(instance);\n"
EXPORT = "module.exports = VoiceEngine;"
INSTALL = "require('./dpe-app-audio.cjs').installVoice(VoiceEngine);\n"
SIDECARS = {
    "native-picker-discovery.cjs": "native-picker-discovery.cjs",
    "native-picker-renderer.cjs": "native-picker-renderer.cjs",
    "native-picker-bridge.cjs": "native-picker-bridge.cjs",
    "native-picker.css": "native-picker.css",
    "app-icons.cjs": "app-icons.cjs",
    "audio-picker.cjs": "audio-picker.cjs",
    "audio-picker.css": "audio-picker.css",
    "wayland-audio.cjs": "dpe-wayland-audio.cjs",
    "pipewire_audio.py": "dpe-pipewire-audio.py",
    "voice-devices.cjs": "dpe-voice-devices.cjs",
    "window_inventory.py": "window_inventory.py",
}
CAMERA_BEFORE = "const asyncVideoInputDeviceInit = process.platform === 'win32';"
CAMERA_AFTER = "const asyncVideoInputDeviceInit = process.platform === 'win32' || (process.platform === 'linux' && process.env.DPE_CAMERA_ASYNC_INIT !== '0');"


def patch(directory: Path, adapter: Path) -> dict:
    wrapper = directory / "index.js"
    before = sha256(wrapper)
    source = wrapper.read_text()
    if source.count(CAMERA_BEFORE) == 1:
        source = source.replace(CAMERA_BEFORE, CAMERA_AFTER)
    elif source.count(CAMERA_AFTER) != 1:
        raise ValueError("Unknown asynchronous camera initialization setting")
    if source.count(ANCHOR) != 1 or source.count(EXPORT) != 1:
        raise ValueError(
            "Unknown voice connection wrapper; application audio adapter not installed"
        )
    if INSERT not in source:
        source = source.replace(ANCHOR, ANCHOR + INSERT, 1)
    elif source.count(ANCHOR + INSERT) != 1:
        raise ValueError("Unexpected existing application audio adapter")
    if INSTALL not in source:
        source = source.replace(EXPORT, INSTALL + EXPORT, 1)
    elif source.count(INSTALL + EXPORT) != 1:
        raise ValueError("Unexpected native picker adapter")
    for name in SIDECARS:
        if not adapter.with_name(name).is_file():
            raise ValueError("Missing application audio helper: " + name)
    native = patch_pulse_audio(directory / "discord_voice.node")
    wrapper.write_text(source)
    shutil.copy2(adapter, directory / "dpe-app-audio.cjs")
    for name, destination in SIDECARS.items():
        shutil.copy2(adapter.with_name(name), directory / destination)
    return {
        "input_sha256": before,
        "output_sha256": sha256(wrapper),
        "adapter_sha256": sha256(adapter),
        "native_buffer_patch": native,
        "enabled_by_default": True,
        "default_sessions": ["wayland"],
        "disable_environment": "DPE_APP_AUDIO=0",
        "helper_sha256": {
            destination: sha256(adapter.with_name(name)) for name, destination in SIDECARS.items()
        },
        "x11_enable_environment": "DPE_APP_AUDIO=1",
        "trace_requires_environment": "DPE_APP_AUDIO_TRACE=1",
        "publishable": False,
    }
