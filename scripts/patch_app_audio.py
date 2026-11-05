"""Install the module adapter; reject an unknown native wrapper layout."""

import shutil
from pathlib import Path

from common import sha256
from patch_capture_chroma import patch as patch_capture_chroma
from patch_capture_dimensions import patch as patch_capture_dimensions
from patch_decoder_eagain import patch as patch_decoder_eagain
from patch_presentation_clock import patch as patch_presentation_clock
from patch_pulse_audio import patch as patch_pulse_audio
from patch_vaapi_hrd import patch as patch_vaapi_hrd
from patch_video_pacing import patch as patch_video_pacing

ANCHOR = "function bindConnectionInstance(instance) {\n"
INSERT = "    instance = require('./dpe-app-audio.cjs').wrap(instance);\n"
VIDEO_INSERT = "    instance = require('./dpe-video.cjs').wrap(instance);\n"
EXPORT = "module.exports = VoiceEngine;"
INSTALL = "require('./dpe-app-audio.cjs').installVoice(VoiceEngine);\n"
SIDECARS = {
    "../video/transport.cjs": "dpe-video.cjs",
    "../video/capture.cjs": "dpe-capture.cjs",
    "../video/capture-upstream.cjs": "dpe-capture-upstream.cjs",
    "../video/capture-scale.cjs": "dpe-capture-scale.cjs",
    "../video/capture-sync.cjs": "dpe-capture-sync.cjs",
    "../video/capture-picker.cjs": "dpe-capture-picker.cjs",
    "../video/capture-backends.cjs": "dpe-capture-backends.cjs",
    "../video/encoder.cjs": "dpe-encoder.cjs",
    "../video/settings.cjs": "dpe-screenshare-settings.cjs",
    "../camera/flip.cjs": "dpe-camera-flip.cjs",
    "../discord-ui/runtime.cjs": "discord-ui/runtime.cjs",
    "../discord-ui/source.cjs": "discord-ui/source.cjs",
    "../discord-ui/ui-runtime.cjs": "discord-ui/ui-runtime.cjs",
    "../discord-ui/PREACT-LICENSE.txt": "discord-ui/PREACT-LICENSE.txt",
    "../discord-ui/components.css": "discord-ui/components.css",
    "../discord-ui/presentation.cjs": "discord-ui/presentation.cjs",
    "../discord-ui/stream-audio.cjs": "discord-ui/stream-audio.cjs",
    "../discord-ui/icons.cjs": "discord-ui/icons.cjs",
    "../discord-ui/BOOTSTRAP-ICONS-LICENSE.txt": "discord-ui/BOOTSTRAP-ICONS-LICENSE.txt",
    "system-tools.cjs": "system-tools.cjs",
    "portal-detection.cjs": "portal-detection.cjs",
    "portal-monitor.py": "portal-monitor.py",
    "capture_detection.py": "capture_detection.py",
    "native-picker-discovery.cjs": "native-picker-discovery.cjs",
    "native-picker-renderer.cjs": "native-picker-renderer.cjs",
    "native-picker-bridge.cjs": "native-picker-bridge.cjs",
    "native-picker.css": "native-picker.css",
    "app-icons.cjs": "app-icons.cjs",
    "audio-picker.cjs": "audio-picker.cjs",
    "search-view.cjs": "search-view.cjs",
    "audio-picker.css": "audio-picker.css",
    "wayland-audio.cjs": "dpe-wayland-audio.cjs",
    "pipewire_audio.py": "dpe-pipewire-audio.py",
    "voice-devices.cjs": "dpe-voice-devices.cjs",
    "window_inventory.py": "window_inventory.py",
    "x11_owners.py": "x11_owners.py",
}
CAMERA_BEFORE = "const asyncVideoInputDeviceInit = process.platform === 'win32';"
CAMERA_AFTER = "const asyncVideoInputDeviceInit = process.platform === 'win32' || (process.platform === 'linux' && process.env.DPE_CAMERA_ASYNC_INIT !== '0');"
FLIP_ANCHOR = "VoiceEngine.initialize({"
FLIP_INSTALL = "require('./dpe-camera-flip.cjs').install(__dirname);\n"
CAPTURE_INSTALL = "require('./dpe-capture.cjs').install(__dirname);\n"
ENCODER_INSTALL = "require('./dpe-encoder.cjs').install(__dirname);\n"
SCALE_INSTALL = "require('./dpe-capture-scale.cjs').install(__dirname);\n"
SYNC_INSTALL = "require('./dpe-capture-sync.cjs').install(__dirname);\n"
BACKENDS_INSTALL = "require('./dpe-capture-backends.cjs').install(__dirname, VoiceEngine);\n"
VIDEO_INSTALL = (
    "require('./dpe-video.cjs').install({capture: require('./dpe-capture.cjs'), "
    "settings: require('./dpe-screenshare-settings.cjs'), encoder: require('./dpe-encoder.cjs'), backends: require('./dpe-capture-backends.cjs'), sync: require('./dpe-capture-sync.cjs'), voice: VoiceEngine});\n"
)


def patch(directory: Path, adapter: Path) -> dict:
    wrapper = directory / "index.js"
    before = sha256(wrapper)
    source = wrapper.read_text()
    if source.count(FLIP_ANCHOR) != 1:
        raise ValueError("Unknown camera engine initialization")
    if FLIP_INSTALL not in source:
        source = source.replace(FLIP_ANCHOR, FLIP_INSTALL + FLIP_ANCHOR, 1)
    elif source.count(FLIP_INSTALL + FLIP_ANCHOR) != 1:
        raise ValueError("Unexpected camera flip adapter initialization")
    if CAPTURE_INSTALL not in source:
        source = source.replace(FLIP_INSTALL, CAPTURE_INSTALL + FLIP_INSTALL, 1)
    elif source.count(CAPTURE_INSTALL + FLIP_INSTALL) != 1:
        raise ValueError("Unexpected capture adapter initialization")
    if ENCODER_INSTALL not in source:
        source = source.replace(CAPTURE_INSTALL, ENCODER_INSTALL + CAPTURE_INSTALL, 1)
    elif source.count(ENCODER_INSTALL + CAPTURE_INSTALL) != 1:
        raise ValueError("Unexpected encoder compatibility initialization")
    if SCALE_INSTALL not in source:
        source = source.replace(ENCODER_INSTALL, SCALE_INSTALL + ENCODER_INSTALL, 1)
    elif source.count(SCALE_INSTALL + ENCODER_INSTALL) != 1:
        raise ValueError("Unexpected capture scale initialization")
    if SYNC_INSTALL not in source:
        source = source.replace(SCALE_INSTALL, SYNC_INSTALL + SCALE_INSTALL, 1)
    elif source.count(SYNC_INSTALL + SCALE_INSTALL) != 1:
        raise ValueError("Unexpected capture synchronization initialization")
    if BACKENDS_INSTALL not in source:
        source = source.replace(SYNC_INSTALL, BACKENDS_INSTALL + SYNC_INSTALL, 1)
    elif source.count(BACKENDS_INSTALL + SYNC_INSTALL) != 1:
        raise ValueError("Unexpected capture backend initialization")
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
        if source.count(ANCHOR + VIDEO_INSERT + INSERT) != 1:
            raise ValueError("Unexpected existing application audio adapter")
    if VIDEO_INSERT not in source:
        source = source.replace(ANCHOR, ANCHOR + VIDEO_INSERT, 1)
    elif source.count(ANCHOR + VIDEO_INSERT + INSERT) != 1:
        raise ValueError("Unexpected video transport adapter")
    if INSTALL not in source:
        source = source.replace(EXPORT, INSTALL + EXPORT, 1)
    elif source.count(INSTALL + EXPORT) != 1:
        raise ValueError("Unexpected native picker adapter")
    if VIDEO_INSTALL not in source:
        source = source.replace(INSTALL, VIDEO_INSTALL + INSTALL, 1)
    elif source.count(VIDEO_INSTALL + INSTALL) != 1:
        raise ValueError("Unexpected screenshare settings adapter")
    for name in SIDECARS:
        if not (adapter.parent / name).is_file():
            raise ValueError("Missing application audio helper: " + name)
    native = patch_pulse_audio(directory / "discord_voice.node")
    video = patch_vaapi_hrd(directory / "discord_voice.node")
    dimensions = patch_capture_dimensions(directory / "discord_voice.node")
    decoder = patch_decoder_eagain(directory / "discord_voice.node")
    chroma = patch_capture_chroma(directory / "discord_voice.node")
    presentation = patch_presentation_clock(directory / "discord_voice.node")
    pacing = patch_video_pacing(directory / "discord_voice.node")
    wrapper.write_text(source)
    shutil.copy2(adapter, directory / "dpe-app-audio.cjs")
    for name, destination in SIDECARS.items():
        (directory / destination).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(adapter.parent / name, directory / destination)
    return {
        "input_sha256": before,
        "output_sha256": sha256(wrapper),
        "adapter_sha256": sha256(adapter),
        "native_buffer_patch": native,
        "native_video_patch": video,
        "native_capture_dimensions_patch": dimensions,
        "native_decoder_patch": decoder,
        "native_capture_chroma_patch": chroma,
        "native_presentation_clock_patch": presentation,
        "native_video_pacing_patch": pacing,
        "enabled_by_default": True,
        "default_sessions": ["wayland"],
        "disable_environment": "DPE_APP_AUDIO=0",
        "helper_sha256": {
            destination: sha256(adapter.parent / name) for name, destination in SIDECARS.items()
        },
        "x11_enable_environment": "DPE_APP_AUDIO=1",
        "trace_requires_environment": "DPE_APP_AUDIO_TRACE=1",
        "publishable": False,
    }
