# Native Wayland application audio

The adapter keeps Discord's native video capture and sends selected application
audio through its existing PID-based input. Routing changes live in the voice
wrapper and helper, so they do not require an Electron rebuild.

For desktop setup and use, see [screen-share audio](../wayland-audio.md).
This page covers source selection, private routes, cleanup and isolated tests.

## Enablement and dependencies

The adapter is on by default on Wayland. `DPE_APP_AUDIO=0` restores upstream
routing; `DPE_APP_AUDIO=1` enables the optional X11 adapter too. Close an existing
instance before changing environment variables. `DPE_APP_AUDIO_TRACE=1` enables
bounded diagnostics, including when the adapter is disabled.

PipeWire, its audio tools and a session manager such as WirePlumber are required,
even with a real PulseAudio server. The app leaves audio services and default
devices unchanged. Missing tools or unresolved selection leave share audio off.
Ordinary microphone/webcam transport options pass through, and picker events do
not start routes on idle connections.

## Source selection

On niri versions exposing the `Casts` IPC request, the helper follows the actual
PipeWire link into the native Discord video consumer. It joins that source node
to niri's cast record, then its target window to the window's process. It includes
that process's audio streams and descendants, checking UID and process start time.
Several audio processes can therefore contribute to one application's mix.
Selecting a monitor explicitly chooses system playback except Discord.

Matching requires that source relationship. Focus, window titles and a lone
active cast are insufficient. A portal video node is not interpreted as a PID.
Native Linux picker callbacks do not reliably expose
a selected application PID, and `screen:0:0` also represents selected windows.
The portal broker can own the passed PipeWire FD; its security PID is not the
native capture consumer's PID.

Other compositors and older niri versions use an explicit **Screen-share audio**
dialog after the native video picker. Choose the playing application, **Entire
system (except Discord)**, or **No audio**. Video stays on Discord's native path;
the dialog selects only audio.
Compositor-independent automatic window/PID discovery is not promised. The
explicit picker lists audio-owning processes; separate siblings can appear as
separate entries. Flatpak/PID namespace remapping is not yet qualified.

Audio selection operates at application level. On niri, browsers such as Zen
use the same automatic process matching as other applications; there is no
extra browser prompt or individual-stream picker. All playback streams of the
selected application and its descendants are included, including its other
windows/tabs. Other applications and Discord playback remain excluded.

Window names in the picker are enriched using Hyprland `hyprctl`, Sway IPC,
optional KDE `kdotool`, or GNOME's optional Window Calls extension. KDE/GNOME
dependency notes appear in the dialog. These tools expose window metadata but
do not establish the exact portal-capture-to-window relationship; the notes
therefore say that audio selection remains manual. No extension is installed,
unsafe Shell Eval enabled, window focused or title used as automatic authority.
See [kdotool's read-only scripting API](https://github.com/jinliu/kdotool#kwinscript)
and [Window Calls](https://github.com/ickyicky/window-calls).

## Audio route and cleanup

1. Selected PipeWire playback nodes are tapped into a private, non-lingering
   null sink. Stream serials prevent following a reused node ID. PipeWire-Pulse
   streams are deduplicated, and Discord plus the helper's descendants are excluded.
2. With a real PulseAudio server, each selected sink input is recorded using
   `parec --monitor-stream` on its sink's monitor and replayed directly into the
   private Pulse sink. This keeps those streams on one server clock. Only native
   PipeWire playback crosses to PulseAudio. The original application's playback
   and the rest of its sink are not redirected or captured.
3. The PipeWire mix feeds `pacat` on a separate private sink. With PipeWire-Pulse,
   Discord receives that playback PID. With real PulseAudio, every private feed
   advertises its owning helper's PID through the standard application property;
   Discord's native API mixes all feeds owned by that PID. Loopback is disabled.
   The ordinary microphone and default devices are unchanged.
4. Audio is silenced before selection changes, cancellation, audio-off, source
   ending and destruction. Generations reject late helper replies. Stdin closure
   releases routes; parent-death signals and a narrowly scoped Pulse module lease
   also clean up after an uncatchable helper crash.

The native PulseAudio mixer can retain unread samples after detachment.
`scripts/patch_pulse_audio.py` clears that buffer under the existing mainloop
lock before attaching a new source. This prevents a brief contribution from the
previous source after sharing resumes. It validates the reviewed controller
instructions, buffer layout and lock targets, then replaces the private module
atomically. Unknown native layouts stop module preparation. The fix also applies
when `DPE_APP_AUDIO=0`; that setting only disables the routing adapter.
See the [reproduction and validation](audio-investigation.md#stale-native-mixer-samples-after-reattachment).

The UUID-tagged `dpe-audio-…-mix/native` sinks and monitors are filtered from
Discord's public device enumeration and device-change callback. This prevents
the internal routes from producing a **New audio devices detected** prompt.
Real device notifications, native device indices and the audio server's default
devices remain intact.

The native libpulse capture path otherwise requests a two-second default buffer
on PulseAudio. The adapter supplies `PULSE_LATENCY_MSEC=40` only if
unset. This is a process-local buffer request, including other libpulse streams
created in that native process; it changes no server configuration. An explicit
user value is preserved. Helper subprocesses use their own explicit buffer sizes.
See [libpulse buffer handling](https://github.com/pulseaudio/pulseaudio/blob/master/src/pulse/stream.c)
and [PipeWire's libpulse environment documentation](https://docs.pipewire.org/page_man_pipewire-pulse_1.html).

## CI validation

`check_wayland_audio.py` launches a disposable niri compositor, private D-Bus and
ScreenCast portal, PipeWire, and either PipeWire-Pulse or real PulseAudio. The
portal selects real native Wayland GTK windows; Discord itself opens the portal
and consumes actual PipeWire video frames. Xvfb supplies only niri's outer software
rendering surface. Discord and the test applications have no X11 display.

### Audio-control regression

The fixture uses the shipped voice wrapper's audio-control defaults and records
`offload_adm_controls` in its result. It previously forced that setting off, unlike
the packaged app. In run `40100767363`, the native engine stalled in
`StartRecording` and the queued device-enumeration callback timed out. A local
repeat also stalled during a source switch. Removing the test-only override uses
Discord's existing dedicated controls thread; the module and adapter patches are
unchanged. Keep the real device-enumeration, private-device positive control and
audio-isolation assertions enabled; retries or longer timeouts would conceal the
configuration mismatch.
See the [local and CI results](../evidence/wayland-audio-defaults.json).

### What the fixture measures

Independent tones distinguish an application's parent and child process,
another application's native PipeWire playback, and simulated received Discord
audio. The PCM assertions cover application selection, switching, whole-monitor
sharing, audio off/on, portal cancellation, application exit and clearing the
source. They also check default devices, leftover nodes/capture streams, and
helper-crash cleanup. The diagnostic RawSamples callback can have gaps, so the
analyzer fits the known tones in contiguous 10 ms frames instead of joining all
callbacks into one FFT. It requires at least 24,000 samples and 90% clean frames
for active cases, and checks the worst unexpected tone in every clean frame.
A regression test verifies that even one 10 ms leak fails. This measures routing
isolation, not end-to-end latency or dropout-free call quality.
An additional case operates the actual audio picker on
Wayland with automatic compositor discovery disabled.

All seven Wayland fixtures (three clients × two servers, plus the selector)
passed in [CI run 40030377851](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/40030377851).
The [retained reports](../evidence/wayland-audio-ci.json) include exact module/helper
hashes, native video events, PCM measurements and cleanup results.

The pinned test compositor supports software-rendered screencasts, so CI needs
no GPU. Its completed binary is cached by source/build configuration and verified
before reuse; intermediate Rust objects are not uploaded. These tests run on
standard GitHub runners, separate from the expensive Electron compile. Completed
reference/candidate builds automatically run the same fixtures using their verified
runtime artifact. They do not cause another Electron compile.

The fixture uses no account, remote Discord UI, network service, physical
microphone or host display/audio socket. It proves the module's native capture
and routing in that environment, not an end-to-end Discord call. Custom-runtime
qualification, live UI arguments, microphone/Krisp during a real share, and actual
GNOME/KDE sessions remain release checks. See [the handoff](handoff.md)
for retained runtime checks and open test gaps.

## Local interactive verification

`check_wayland_audio.py --selection interactive` pauses after each video choice
so an operator can inspect and operate the actual audio dialog. The disposable
container writes `operator-env.json` for `niri msg`/`wtype`; after selecting a row
or cancelling, create `audio-choice-done` in its output directory. The harness
then measures captured tones. `manual` remains the automated selector test used
by CI. Neither mode mounts the host desktop or audio sockets.

On 23 October the actual dialog was visually inspected and operated for both
apps, system sharing, cancellation and re-enabling. All nine audio phases and
crash cleanup passed with `DPE_APP_AUDIO` absent. An additional automatic niri
case passed with real PulseAudio plus PipeWire. See
[local evidence](../evidence/camera-wayland-local.json). These are native module tests
with a controlled portal, not an account-connected Discord call.

## Compiled prerelease result

Electron 46.0.0-alpha.1 from compiler run `40053107555` passes all seven native
Wayland fixtures and the three X11 channel fixtures in audio run `40055239278`.
Reports verify the custom executable hash, selective audio, multiple processes,
source changes, cancellation and cleanup. Both PipeWire-Pulse and real PulseAudio
plus PipeWire pass, including manual app selection. See
[the retained results](../evidence/prerelease-runtime-local.json). These synthetic
fixtures do not establish a real Discord call or GNOME/KDE desktop behavior.

The 24 October fixes additionally pass local native tests with PipeWire-Pulse
and real PulseAudio, including a positive control showing the internal sink in
raw enumeration and no leaks through the filtered API. See
[local fix evidence](../evidence/desktop-fixes-local.json), which also retains
historical results for the subsequently removed stream-picker experiment.
The restored application-level routing was checked again with automatic niri
matching and the manual app picker on real PulseAudio plus PipeWire. Current
unit coverage checks that all streams of one PID are included while another
application remains excluded; see [restoration evidence](../evidence/automatic-audio-restored.json).
