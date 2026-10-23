# Native application-audio investigation

Static inspection on 23 October 2026 found an existing Linux application-audio
path in all three locked `discord_voice.node` binaries. No module was loaded,
no client was launched and no personal profile or microphone was accessed.
The binaries differ; reports identify each by its own SHA-256:

- [Stable](evidence/stable-voice-interface.json)
- [PTB](evidence/ptb-voice-interface.json)
- [Canary](evidence/canary-voice-interface.json)

## Observations

| Native boundary | Observed field references or calls |
| --- | --- |
| Connection `SetTransportOptions` | `soundsharePid`, `soundshareLoopback`, `soundshareDiscardRearChannels` |
| `SetDesktopSourceWithOptions` | `sourceId`, `type` |
| `SetGoLiveDevices` | `videoInputDeviceId`, `audioInputDeviceId` |
| PulseAudio sink identification | `application.process.id` |
| PulseAudio client identification | `application.process.id`, `pipewire.sec.pid` |
| PulseAudio stream monitor | `pa_stream_new`, `pa_stream_set_monitor_stream`, `pa_stream_connect_record` |
| PulseAudio attachment | Enumerates sink inputs through `pa_context_get_sink_input_info_list` |

These names are referenced by the corresponding native functions, rather than
merely appearing somewhere in the binary. The existing JavaScript wrapper
forwards connection transport options, source options and Go Live devices to
the native instance. In Stable's monitor function, disassembly shows the monitor
selection call before the recording connection call. PulseAudio documents that
this API restricts recording to a specific playback stream when connected to a
monitor source. [PulseAudio API](https://www.freedesktop.org/software/pulseaudio/doxygen/stream_8h.html)

The evidence supports testing the existing native path first. It does **not**
prove that the UI supplies a usable PID, that loopback is disabled for window
sharing, that PID namespaces or child processes match, or that the server honors
the requested stream isolation. The `SetGoLiveDevices` device fields are another
possible boundary; their interaction with video and microphone capture remains
unverified. No audio patch or helper is enabled on this evidence alone.

## Next experiment

Use an isolated test client to record only the source selector and the allowlisted
soundshare fields at those wrapper boundaries. Compare window and monitor sharing,
audio enabled/disabled and source switches. Do not serialize the full transport
options object: it also contains connection/encryption-related settings.

Against an isolated PulseAudio/PipeWire session, produce different tones in two
test applications. Compare the supplied soundshare PID with their playback-stream
properties and the chosen video source. Test correct PID with loopback disabled,
wrong PID and PID zero, plus child-process and namespace cases. Verify captured
audio and microphone independence. Only then choose a small wrapper correction,
a checksum-gated native fix or a venmic adapter. Preserve failure/cleanup behavior
and never silently substitute system audio when application identity is unknown.

To reproduce the static report from a checksum-verified extracted module:

```sh
.venv/bin/python scripts/inspect_voice.py /path/to/discord_voice.node \
  --report out/voice-interface.json
```

The inspector uses ELF symbols, instruction references and `objdump`; it never
executes the input. Reports explicitly keep runtime and application-isolation
validation false. Missing or changed symbols remain visible in the report.

## Implemented module adapter and isolated audio tests

The opt-in adapter in `integration/audio/app-audio.cjs` corrects the existing
native capture options. `scripts/patch_app_audio.py` inserts one call at the
voice module's connection-wrapper boundary and refuses an unfamiliar layout.
No compiled voice binary or remote client UI is modified. Packaging installs
the adapter and records its hashes; it remains disabled unless
`DPE_APP_AUDIO=1` is set for that package's launcher.

The isolated experiment confirmed that PID **1** selects other applications'
playback, while a real application PID selects its own streams. This is a native
API observation; it does **not** establish which value the live Discord UI sends
for every window/portal selection. On local X11, the adapter obtains the selected
window's `_NET_WM_PID`, verifies the process owner and start time, and resolves a
single audio-owning descendant from PulseAudio's stream properties. It forces
per-process capture rather than loopback for that window. Missing/ambiguous
identity disables application audio. Explicit whole-screen sharing retains the
client's system-audio choice. Source switches, disabling audio, clearing the
source, source-ended callbacks and connection destruction clear capture.

The design was compared with [Vesktop's venmic integration](https://github.com/Vencord/Vesktop/blob/da2121d7dc9d13fd58cdb4df4013b23027ee0d5b/src/main/venmic.ts)
and [capture-track adapter](https://github.com/Vencord/Vesktop/blob/da2121d7dc9d13fd58cdb4df4013b23027ee0d5b/src/renderer/patches/screenShareFixes.ts).
Vesktop selects PipeWire streams and feeds a virtual recording device into its
browser media stream. Its source-selection and exclusion behavior are useful
references, but that browser-track replacement does not directly apply to the
official client's native path. A local venmic 7.2.3 experiment created its virtual
source; an independently selectable native Go Live device was not established.
No venmic binary, browser fallback or unverified device redirect is shipped.

`check_app_audio.py` creates two disposable X11 applications playing 440 Hz and
880 Hz into a private PipeWire sink. It loads the **packaged JavaScript voice
wrapper** with the real native module in a copied bundled runtime. The test
covers the system-audio positive control, A-only, switching to B, disabled audio,
unknown window, re-enabling A, application exit and clearing the source. It
inspects captured PCM frequency content and verifies that capture streams are
gone after exit. The fixture has no network interface except loopback, no Discord
account/remote UI and no physical audio devices. Its synthetic microphone is
not evidence of a live microphone call. Production sandbox settings are unchanged.

These tests passed locally for Stable 1.0.166, PTB 1.0.224 and Canary 1.0.2357.
`app-audio.yml` repeats them in a network-disabled Arch container on standard
GitHub runners. The fixture's tiny tone program is compiled there; it does not
allocate the Electron compiler or Blacksmith. Successful reference/candidate
compiler runs also trigger this workflow against their verified runtime
artifact, reusing the same three-client tests. See
[local audio evidence](evidence/app-audio-local.json).

### Remaining limitations

This is **not a complete or default-enabled per-app audio release**. Full
Wayland/portal source identity and live UI arguments have not been verified.
A supplied real process hint can be used when an X11 identity is unavailable,
but it is not treated as tested Wayland support. Multiple audio-owning descendant
PIDs are rejected; mixing them requires a proven native adapter for a selective
capture helper. A single audio-owning child and multiple streams from one PID
fit the existing native API. Testing with a custom runtime, a real call,
microphone independence and supported multi-process/Wayland applications remains
required before enabling the feature by default. The adapter never substitutes
system audio for an unresolved application.
