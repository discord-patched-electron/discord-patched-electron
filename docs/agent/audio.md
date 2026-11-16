# Application audio

User setup: [Wayland audio](../wayland-audio.md). Implementation:
`integration/audio/`, native boundary patching in `scripts/patch_app_audio.py`.
Changes normally reuse Electron; prepare a new immutable module generation and
matching scopes when native bytes change.

## Native boundary and enablement

The native connection accepts `soundsharePid`, `soundshareLoopback` and
`soundshareDiscardRearChannels`; source/device APIs have separate roles. Static
symbols alone do not prove isolation. Isolated experiments established PID 1 as
other-app/system playback and a real PID as that app's streams. Do not serialize
full transport options: they also contain connection/encryption state.

The Wayland adapter is default-on; `DPE_APP_AUDIO=0` restores upstream routing and
`DPE_APP_AUDIO=1` enables the optional X11 adapter. `DPE_APP_AUDIO_TRACE=1` provides
bounded diagnostics. PipeWire tools and a session manager remain needed with
real PulseAudio. Never change host services, default devices or microphone routes
to make a fixture pass. Missing tools or unknown identity leave audio off.

Unexpected helper exit or spawn failure emits one bounded warning and silences
capture. Fatal Python failures report only exception type, function and line;
protocol contents and exception text stay out of the renderer log. Intentional
teardown is quiet. These diagnostics require the updated adapter on a subsequent
launch; they cannot recover the history of an already running connection.

## Identity and automatic selection

Require exactly one source linked to this Discord native consumer. A portal node
ID, its broker PID, focused window, title or lone active cast cannot identify an
application. Niri joins PipeWire source→Casts→target window→live process. Bind
PID/start-time/UID, then include descendants; parentage changes alone must not
invalidate a surviving app. Reuse/UID changes do invalidate it.

A bounded portal observer accepts known successful single-stream response shapes:
standard monitor source_type=1, reviewed GNOME/KDE/Hyprland restore metadata,
Mutter RecordWindow reply-cookie/stream-path joins and KWin exact stream names.
Only app/window/stream identifiers survive parsing, never titles, restore tokens
or raw messages. Observe for at most two minutes, expire unbound records after
15 s, and bound record count/size/nesting. A parent-death guard stops the monitor
on renderer exit. Unsupported versions, multiple sources or ambiguity stay manual.

A 1.2 s grace period waits for source metadata, not sound. A positively identified
live app can remain silent indefinitely and later acquire child playback. Never
restore the old playback timeout. `audioAutoDetect` and `audioSkipScreenPicker`
are independent; disabling both or `DPE_AUDIO_AUTO_DETECT=0` forces the picker.
A confirmed monitor permits system playback except Discord; a generic screen ID,
region or virtual source does not establish that authority.

Compositor inventories and playback process/app metadata can corroborate exact
app IDs. Separate unrelated process families with the same ID are ambiguous even
if only one plays. Flatpak uses a verified live instance/bwrapinfo host PID and
matching `.flatpak-info`, never a namespace PID. Missing mapping stays unavailable.
Desktop Flatpak/Snap playback remains unqualified on unsupported metadata paths.

For Niri's XWayland satellite proxy, use server-verified XRes 1.2 owner IDs and
exact WM_CLASS semantics, not client-supplied `_NET_WM_PID`. The WM_S selection's
live PID/start-time must match the proxy, guarding against another DISPLAY.
Compare two complete owner/class snapshots, bound retries/timeouts and reject
unrelated same-class instances. Cache only the selected PID/start-time; never
remap it after exit. Remote DISPLAY, missing XCB/XRes and uncertain owners fall
back to manual selection. Window titles enrich the picker but grant no authority.

## Picker and ownership

The Preact Share audio modal selects a playing app, explicit system audio or
Continue without audio; native dialog remains fallback. It changes audio only,
leaving native video selection intact. Once manual choice/cancellation begins,
late portal/niri/native hints cannot override it. Refresh updates options without
closing the picker. A new source/generation resets the decision.

Use the shared Discord discovery/runtime/serializer and replica controls. Keep
search, keyboard selection, zoom, long labels, focus and scrolling stable. Do not
invoke arbitrary webpack factories or infer a new choice from playback starting.
Owner/request/generation checks reject stale replies. Idle connections do not
start routes. App-level selection includes the app's other tabs/windows and
playback descendants, not unrelated apps or Discord itself.

Native picker cancellation/error callbacks cancel only a pending picker. Once
selection completes, a late callback must not mark the accepted source inactive
or kill its helper: that also prevents audio toggles from restarting it. Keep
forwarding callbacks to Discord. Source end, clear and native picker stream
release remain authoritative teardown paths.

## Routing and teardown

Private capture routes join PipeWire/Pulse streams by object serial and validated
process identity. Multiple audio processes contribute to one app mix; keep each
Pulse server's clock rather than driving streams from another server. Exclude
DPE/Discord and private helper devices. Temporary sinks/links, streams, selectors
and observers are owned by one generation; cancellation, source replacement,
helper exit and connection destruction silence the old route and release them.

X11 setters silence the prior selection before switching and commit the new
selection only after native acceptance. On exception clear state/hints/timers,
remain silent and propagate the error. Do not widen to system capture on failure.
The original injected rejection test did not prove live audio leakage.

The Pulse mixer keeps capture buffers scoped to each stream/server and resets
buffer state on route changes. Microphone hotplug's invalid channel/volume guard
is a separate [native patch](native-media.md#native-patch-chain), not a video
frame failure. Avoid forced `offloadAdmControls=false`: native StartRecording
stalled in that fixture mode; six unchanged-input runs passed after restoring
the wrapper's dedicated-control default. Stop/flush fixture recorders per phase
and wait for positive-control device enumeration before measuring leakage.

### Sender buffering limits

With PipeWire-Pulse, selected streams feed the private mix through
`pw-loopback`; a second loopback connects that mix directly to the private native
sink on the same graph. Both bridge ends request 48 kHz stereo and a 20 ms
latency. There is no `pw-cat`/OS pipe/`pacat` FIFO between the mix and native sink.
[PipeWire's loopback](https://github.com/PipeWire/pipewire/blob/1.6.11/src/modules/module-loopback.c)
uses the newest capture buffer per playback cycle and flushes capture/playback
buffers on pause. No `target.delay.sec` is set, so its optional delay ring stays
disabled. These are scheduling contracts, not measured end-to-end latency.

The bridge capture uses the exact private mix serial with sink capture enabled;
playback uses the exact private native sink serial. Both disable reconnection so
source loss cannot redirect to a default device. Capture is passive; playback
stays active like the former `pacat` stream. Playback must set `node.virtual =
false`: [PipeWire-Pulse hides the client index of virtual streams](https://github.com/PipeWire/pipewire/blob/1.6.11/src/modules/module-protocol-pulse/pulse-server.c),
preventing Discord from resolving the worker PID. Native capture follows only
that worker's PID. The owned worker, private sinks and selected-app feeds retain
the route's parent-death and teardown guards.

Do not publish the worker PID on spawn. Wait for its exact private playback
node to run and its Pulse sink input to expose a valid client index, the private
native sink, and uncorked/unmuted state. Refresh Pulse inputs during this handoff
even without a subscription event; resume event-cached snapshots after readiness.
An early native attachment can remain failed while the worker later becomes
healthy. Preserve the selection and route while waiting rather than asking for
consent again or widening capture.

Real PulseAudio keeps its separate-server raw bridge and per-stream fallback
feeds; it cannot use this same-graph connection. Its `pw-cat`→`pacat` bridge still
requests 80 ms on each side, without sample-age metadata or stale-audio recovery.
`Route.spawn` removes `PULSE_LATENCY_MSEC` from helper children, so native
Discord's 40 ms default does not override those requests. This remains an
unqualified latency limitation for the real PulseAudio backend.

On PipeWire, also watch for this native owner's recording node targeting the
exact bridge serial or linked from that bridge. An unrelated microphone or
another Discord connection cannot satisfy this check. Allow five seconds for
asynchronous startup, then clear the native PID for one helper tick and reattach
the same bridge. Permit at most three retries per route; exhaustion silences
capture and reports a recorder failure. Keep manual consent, process identity
and feeds throughout. Cancellation, source replacement and teardown still win.
This detects missing recording endpoints, not PCM progress or queue age.

Canary 1.0.2483's `PulseConnect` previously returned whenever its mainloop
existed, including after the context failed. A PID retry or new source therefore
could not recover. `patch_pulse_reconnect.py` recreates failed/terminated contexts
under the existing [recursive mainloop lock](https://www.freedesktop.org/software/pulseaudio/doxygen/threaded_mainloop.html),
after detaching old streams. Healthy
and connecting contexts remain attached; the mainloop starts once. The exact
whole-module patch applies after pacing, and re-preparation preserves it. Older
reviewed clients retain their connection behavior pending a separate port. Real
PulseAudio retains child-exit checks without graph-based recorder recovery.

An unchanged target keeps its route and feeds; restarting viewing does not reset
sender audio. Child stderr is discarded. The PipeWire change removes the raw FIFO
mechanism for this backend but has no live A/V qualification and does not
establish the cause of the intermittent AV1/Windows report.

Static disassembly of the prepared Canary 1.0.2483 voice module (SHA-256
`42b32e010c1bbd57bc0eb71b0b751730738d85e282c920f331ffabcb3ef035e5`,
recognized by `native_client_2483.py`) shows a separate 192,000-byte native
mixer ring: one second of capacity at this format, not a fixed one-second delay.
`SamplesReady` and `Read` advance sample cursors without sample-age metadata;
reads clear consumed slots. The existing patch clears the ring when attaching
a different PID, not continuously; native uncork handling also realigns a
stream's write cursor. This native ring is separate from the removed PipeWire
raw bridge and the retained real PulseAudio bridge. Source copies and disassembly
are under `out/av-sync-investigation/static/`.

## Validation and limits

Use `scripts/check_app_audio.py` for wrapper/picker/native contracts and
`scripts/check_wayland_audio.py` for portal video plus selective audio. Run both
PipeWire-Pulse and real PulseAudio for routing changes. Owned two-tone fixtures
must establish selected-app capture, nonselected/Discord exclusion, child audio,
late/paused playback, switching, cancellation, stale IDs and helper-crash cleanup.
Use private audio services and fresh profiles; do not drive account calls.

Isolated automatic and manual selection passed all ten PipeWire audio phases with
Electron `46.0.0-alpha.10` (executable SHA-256
`802ff9e279c229f100cea73fb3b2bb161f1a54139041c0b24bbe974cbed70d18`)
and prepared Canary `1.0.2483` voice module SHA-256
`3d67b22c8c69652420fcf299d65ca49cc8c8d4f938a9c16718842e851de683b4`. The fixture
sets audio before the split `prepicked:0` source and repeats the source setter.
PipeWire uses a separate `pipewire-pulse` process and a synthetic mono microphone as
the default recording source, rather than making a sink monitor the default.
It disconnects only the owned `discord_capture` client after a positive audio
phase. The previous native module remained silent through subsequent source
changes, including with helper retries. The corrected native module reconnects
and recovers both selected tones while excluding other-app and Discord playback;
later switching/cancellation/cleanup phases pass. Executable instruction tests
cover cold startup, all context states, lock balance and one-time loop startup.
The replacement stays within `PulseConnect`, with matching unwind metadata;
the executable fixture also unwinds through that body.
Native bridge and offline Krisp checks also pass on this corrected module.

A prior isolated run, using the previous native module identified in the mixer
section, passed synthetic tones through Minecraft's bundled OpenAL library
(SHA-256 `0a8e30eceaf0b69220cb91e6c24d75f57cee7304adac4b77c3430dd454fc7c4f`)
with `ALSOFT_DRIVERS=pipewire`. In the running dev profile, the Java feed and
bridge were present and linked. Native logs showed attachment to the correct
worker while corked, then a failed Pulse context and zero outgoing audio packets;
there was no native recording stream in the graph. The readiness gate prevents
premature PID handoff; bounded retries plus context reconstruction recover the
reproduced failed-context condition. The live session remains unchanged, so
Minecraft recovery still requires confirmation after launching the corrected
checkout. These checks do not identify what originally disconnected its context.

[Natively isolated evidence](../evidence/wayland-audio-ci.json),
[mixer reset](../evidence/pulse-mixer-buffer-reset.json) and
[default-control correction](../evidence/wayland-audio-defaults.json) retain
inputs and results. `tests/test_audio_confidence.py` covers real synchronizer
logic with synthetic proxy/libmpv topologies; XRes has private Xvfb controls.
These establish specific identity/routing contracts, not microphone/Krisp
continuity in every live call, all desktop/portal versions or sandboxed apps.
