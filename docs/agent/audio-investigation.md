# Native application-audio investigation

**Wayland is the primary target; X11 is additional support.** The maintainer
clarified this on 23 October 2026. The initial X11 experiment below established the native audio boundary. The
subsequent [Wayland implementation](../wayland-audio.md) now covers native portal
video, application routing, multi-process mixing and teardown in private fixtures.

Static inspection on 23 October 2026 found an existing Linux application-audio
path in all three locked `discord_voice.node` binaries. No module was loaded,
no client was launched and no personal profile or microphone was accessed.
The binaries differ; reports identify each by its own SHA-256:

- [Stable](../evidence/stable-voice-interface.json)
- [PTB](../evidence/ptb-voice-interface.json)
- [Canary](../evidence/canary-voice-interface.json)

## Observations

| Native boundary                  | Observed field references or calls                                          |
| -------------------------------- | --------------------------------------------------------------------------- |
| Connection `SetTransportOptions` | `soundsharePid`, `soundshareLoopback`, `soundshareDiscardRearChannels`      |
| `SetDesktopSourceWithOptions`    | `sourceId`, `type`                                                          |
| `SetGoLiveDevices`               | `videoInputDeviceId`, `audioInputDeviceId`                                  |
| PulseAudio sink identification   | `application.process.id`                                                    |
| PulseAudio client identification | `application.process.id`, `pipewire.sec.pid`                                |
| PulseAudio stream monitor        | `pa_stream_new`, `pa_stream_set_monitor_stream`, `pa_stream_connect_record` |
| PulseAudio attachment            | Enumerates sink inputs through `pa_context_get_sink_input_info_list`        |

These names are referenced by the corresponding native functions, rather than
merely appearing somewhere in the binary. The existing JavaScript wrapper
forwards connection transport options, source options and Go Live devices to
the native instance. In Stable's monitor function, disassembly shows the monitor
selection call before the recording connection call. PulseAudio documents that
this API restricts recording to a specific playback stream when connected to a
monitor source. [PulseAudio API](https://www.freedesktop.org/software/pulseaudio/doxygen/stream_8h.html)

This initial static evidence supported testing the existing native path first.
It did **not**
prove that the UI supplies a usable PID, that loopback is disabled for window
sharing, that PID namespaces or child processes match, or that the server honors
the requested stream isolation. The `SetGoLiveDevices` device fields are another
possible boundary; their interaction with video and microphone capture remains
unverified. The runtime experiments below were required before installing the opt-in helper.

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
This initial implementation left the compiled voice binary unchanged; the later
buffer-reset correction is documented below. Packaging installed
the adapter and recorded its hashes; it remained disabled unless
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
[local audio evidence](../evidence/app-audio-local.json).

## Implemented Wayland path

The follow-up implementation now resolves the exact native PipeWire video link
through niri's cast/window IPC and mixes the application's audio subprocesses.
Other compositors have an explicit audio-app selector. Both PipeWire-Pulse and
PulseAudio server configurations use a private PipeWire mix and retain native
Discord video. See [Wayland implementation and validation](../wayland-audio.md) for
the route, lifecycle, test fixture and remaining live-call/custom-runtime checks.

The key observation was the module-level native picker API:
`setNativeScreenSharePickerCallbacks`, `presentNativeScreenSharePicker`, and
`releaseNativeDesktopVideoSourcePickerStream`. Linux selection returned
`[false, null]` rather than an application PID; portal cancellation reached the
failure callback. The actual PipeWire graph and niri cast identity establish
which window was selected without relying on the opaque `screen:0:0` token.

The [ScreenCast portal contract](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.ScreenCast.html)
does not guarantee the selected application's PID. niri's
[cast IPC types](https://github.com/YaLTeR/niri/blob/e19df12cf81ffe805609fee497da1a097066ad2f/niri-ipc/src/lib.rs)
provide the exact relation for automatic selection. Vesktop's
[separate audio-source picker](https://github.com/Vencord/Vesktop/blob/da2121d7dc9d13fd58cdb4df4013b23027ee0d5b/src/renderer/components/ScreenSharePicker.tsx)
provided the reference for the explicit selection path on other compositors.

### Collecting the module trace when a real test package is ready

The packaged wrapper now supports `DPE_APP_AUDIO_TRACE=1` independently of
`DPE_APP_AUDIO=1`. To observe the original client arguments, explicitly set `DPE_APP_AUDIO=0`. After closing that same package's existing instance, launch it from a
terminal, for example **once that package exists**:

```sh
DPE_APP_AUDIO=0 DPE_APP_AUDIO_TRACE=1 \
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

## Stale native mixer samples after reattachment

PR #1's [application-audio run 40230030029](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/40230030029)
failed the real PulseAudio Wayland `re-enabled` phase on 25 October 2026.
The selected app's two tones were present, but one clean 10 ms frame also
contained the previous system share's 997 Hz tone at full amplitude. The same
failure reproduced in one of three local runs using the locked Canary module,
bundled Electron 44.9.3 and the retained isolated audio container.

Inspection found a 192,000-byte signed-16-bit mixer ring at controller offset
`0x286`. `SamplesReady` adds samples into its slots; `Read` clears consumed slots.
Detachment disconnects streams but leaves unread slots intact. Reattachment can
therefore mix old samples into a new source. Resetting routing and waiting longer
does not establish that those slots have been cleared.

`scripts/patch_pulse_audio.py` inserts a buffer reset into `Attach`, after its
existing PulseAudio mainloop lock and before the new PID/activation stores. It
reuses the optional attachment log's instruction space for a trampoline, leaving
the same-PID and zero-PID short circuits intact. One native attachment log is
suppressed. No extra allocation, dependency, audio device or Electron build is
needed. The original downloaded archive is retained unchanged.

The patch requires x86_64 ELF symbols and exact normalized instructions for
`Attach`, the constructor, `Read`, `SamplesReady` and `MonitorSinkInput`. Only
relocated calls, external tail jumps and RIP-relative addresses are normalized;
buffer offsets, sizes and control flow remain checked. Lock/unlock calls must
resolve to the expected PulseAudio symbols. An unfamiliar layout or partial
patch is rejected before writing; replacement is atomic and idempotent.
Packaging and module-cache identities include the patcher. Fixture identities
now hash the module after patching, with original and final hashes in the report.

The locked module (`d7937236a8bc448aebe68b9a2819eb255d45e0457a4adaf80582d4f5fc3045a5`)
and the current downloaded Canary module have the same reviewed layout after
relocation normalization. Five consecutive isolated Wayland/PulseAudio trials
passed with the reset. The analyzer, sample windows, settle delay and leakage
thresholds were unchanged. A deterministic executable ELF fixture demonstrates
retained samples before patching and clearing under lock afterward; it also
checks repeated attachment, exact edit bounds, metadata preservation, unknown
layouts, wrong lock targets, stripped modules and partial patches. Synthetic
fixtures do not establish real-call behavior.

The final integrated patch passed Wayland with both PulseAudio and PipeWire,
the manual audio selector, and the X11 fixture. Preparing a private copy of the
current downloaded module also preserved the camera probe's matching identity.
All 189 Python and 51 JavaScript tests and lint passed. See the
[retained validation summary](../evidence/pulse-mixer-buffer-reset.json).
