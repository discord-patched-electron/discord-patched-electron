# Native application-audio investigation

**Wayland is the primary target; X11 is additional support.** The maintainer
clarified this on 23 October 2026. The X11 experiment below establishes a useful
native audio capability, not completion of the requested Wayland feature.

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

## Wayland-first work order

The official wrapper declares `native_screenshare_picker` when both the session
type and display indicate Wayland. Its connection interface includes
`presentDesktopSourcePicker` and source-status/soundshare callbacks. A portal
selection can therefore happen inside the native module, beyond the earlier
`setDesktopSource` call. Treat its original caller arguments and subsequent
callbacks as separate observations.

The [ScreenCast portal contract](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.ScreenCast.html)
returns stream identifiers, source type and optional metadata; it does not
guarantee the selected application's PID. Its `parent_window` identifies the
requesting application's window, not the selected target. A video node's producer
can be the compositor, not the application whose sound should be captured.
Never interpret a portal node ID as a PID or an X11 window ID. The adapter now
refuses X11 PID lookup whenever the session indicates Wayland, even when DISPLAY
also exists for Xwayland.

Vesktop's [audio source picker](https://github.com/Vencord/Vesktop/blob/da2121d7dc9d13fd58cdb4df4013b23027ee0d5b/src/renderer/components/ScreenSharePicker.tsx)
provides explicit inclusion/exclusion selections separately from video. Its
working audio routing does not establish automatic video-window-to-audio-PID
mapping for the official client's picker. Reuse that routing design if needed;
do not assume importing venmic alone solves the identity boundary.

The current desktop reports niri 26.04 (v26.04-214-ge9804c1c), with a `casts` IPC
request available. Upstream [niri IPC types](https://github.com/YaLTeR/niri/blob/main/niri-ipc/src/lib.rs)
describe a cast's `pw_node_id`, session/stream IDs and target window ID; window
records expose an optional PID. This suggests an exact mapping for niri once
Discord's actual selected PipeWire node is established. It is not implemented
or proven yet. Do not select the only active cast, the focused window, or a
matching window title: another application or capture session could own it.
Do not interpret the cast's consumer PID as the captured application's PID.

Resume in this order:

1. Trace the official native picker on Wayland with the audio correction off.
   Establish whether a usable process hint or stream/session identity actually
   crosses the existing wrapper boundary. Include selection, cancellation,
   window-to-window and window-to-monitor switching, and audio off/on.
2. If identity is missing, inspect the native portal response/capture boundary
   and expose only the necessary identity through a narrow, verified module or
   runtime change. A compositor-specific resolver must correlate the exact
   selected stream and reject missing/reused identities. Do not guess from time
   proximity or interpret an opaque `screen:` token as proof of monitor sharing.
3. Use niri's explicit cast/window relation where supported; evaluate other
   compositor backends separately. Preserve native video and microphone devices.
   Resolve audio-owning descendants and use selective routing only if the native
   single-PID capture path cannot represent the chosen application.
4. Add a private Wayland compositor/portal fixture with native Wayland test
   windows and two distinguishable audio streams. Assert the compositor backend
   and portal selection actually used; Xvfb or setting WAYLAND_DISPLAY is not a
   Wayland test. Include concurrent unrelated casts, source replacement, cancel,
   restart, multi-process applications and cleanup, then verify a real call with
   microphone/Krisp active on the custom runtime.

### Collecting the module trace when a real test package is ready

The packaged wrapper now supports `DPE_APP_AUDIO_TRACE=1` independently of
`DPE_APP_AUDIO=1`. For the initial Wayland investigation leave the correction
off. After closing that same package's existing instance, launch it from a
terminal, for example **once that package exists**:

```sh
env -u DPE_APP_AUDIO DPE_APP_AUDIO_TRACE=1 \
  discord-stable-stable-patched-electron-bin 2>&1 \
  | rg --line-buffered '^\[dpe-app-audio-trace\]' \
  | tee wayland-audio-trace.log
```

No installable custom runtime is available yet; this is a resume procedure, not
a claim that the command can run today. A separately prepared disposable test
client with the wrapper installed can use the same environment switch.
The trace uses connection-local sequence numbers, numeric source/PID fields,
known source types and audio-enabled/loopback values. Unknown strings, device
names, titles and arbitrary objects are omitted or redacted. Callback values
are restricted to the same scalar/source allowlist. Full transport options,
credentials, keys, addresses, microphone samples and callback payloads are never
serialized. Diagnostic-only calls preserve native arguments, returns and
callbacks; logger failures cannot interrupt them. The trace is emitted by the
module's JavaScript context; if that context's console is not forwarded to the
terminal, inspect only the matching prefix in its development console. An empty
terminal log is not evidence that the client supplied no source information.
