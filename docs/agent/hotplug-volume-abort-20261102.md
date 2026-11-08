# Camera hotplug renderer abort: native Pulse microphone volume

The latest camera plug/unplug renderer cores all aborted on `AdmHelper` inside
`pa_cvolume_set`, called by
`webrtc::AudioMixerManagerLinuxPulse::SetMicrophoneVolume`. The latest core was
PID 34784, 2 November 2026 at 23:50 UTC. Only crash metadata and the failing stack
were inspected; no conversation or camera contents were extracted.

The native method fetches source information during device changes, then passes
its cached channel count into Pulse without validating it. Pulse aborts on an
invalid channel count or volume. This is an audio-device reconfiguration path,
not a camera-frame or custom screenshare buffer failure. A camera may also expose
microphone devices. Monitor audio hotplug can reach the same audio configuration
code, but no monitor-triggered crash was established.

The existing exact-layout Pulse patch now guards that method after its info
lookup, while the existing mainloop lock is held. Counts outside 1–32 or invalid
volumes return the method's normal error result through its unlock epilogue.
Valid mute, normal and amplified volumes remain unchanged. It does not invent a
channel count, clamp user volume, disable device notifications or hide hardware.
The guard uses reviewed entry-log space and preserves native object/buffer ABI.
Unknown or partially patched layouts are rejected before writing.

Seven native fixture tests execute the patched instructions with deterministic
Pulse function-table stubs, including invalid and valid channel/volume values,
lock balance, untouched surrounding state and idempotence. A retained real native
module was patched twice successfully at `out/hotplug-volume/discord_voice.node`;
`patch-report.json` records hashes. There was no Electron build or physical
hotplug test, and no live process or mapped native inode was modified.

For staging, copy `scripts/patch_pulse_audio.py` into the packaged app's scripts.
That file already participates in the updater compatibility key, so the next
normal startup creates a new immutable module generation with regenerated native
scope/hash manifests. Do not overwrite an installed voice module in isolation:
its camera/capture/encoder scope records must match the new bytes.
