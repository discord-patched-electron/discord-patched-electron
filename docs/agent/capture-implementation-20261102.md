# Capture implementation and validation, 2 November 2026

Status: a native resolution-adaptation stall has been reproduced and fixed;
bounded hardware/load checks passed. The original severe bitrate-collapse
symptom remains unverified. Earlier passes kept the active session undisturbed;
the user subsequently authorized hardware/load tests and a private voice-channel
check. Most checks use fresh synthetic profiles; the explicitly authorized live
check and owned-window compositor tests are qualified below. The installed
launcher has not been changed.

The current candidate includes corrected capture dimensions and H.264 HRD,
GPU pixel-limit adaptation, compatible initial producer-rate preferences,
early frame pacing, native AMD AV1 sizing and AV1 level compatibility, truthful
codec discovery, and Screensharing settings with live diagnostics. Pacing is
enabled by default for active PipeWire shares and can be disabled in settings.
The sections below preserve the investigation stages; their initial opt-in and
unvalidated descriptions are historical. Final delivery is recorded at the end.

The [research report](capture-methods-20261102.md) defines the implementation
gates. This work starts with a measurable native handoff and queue behavior.

## Initial implementation, before hardware validation

- `integration/video/capture.c`: a pre-initialization adapter for the reviewed
  Linux PipeWire callback. Records buffer type, missing timestamps, processing
  submission duration and queue behavior. An optional latest-frame mode skips
  superseded full frames. Cursor/control/unfamiliar metadata and explicit-sync
  buffers retain FIFO processing. PipeWire Busy metadata retains the native
  release path. No pixel mapping or conversion is added.
- `scripts/capture_scope.py`: derives the exact dequeue/release call sites for
  four reviewed module hashes (two upstream revisions, before/after the staged
  HRD correction). Unknown modules retain native behavior. JS verifies the
  prepared file hash and the native installer verifies live call targets.
- Build/updater staging includes capture metadata and the optional adapter.
  `DPE_CAPTURE_PIPEWIRE=observe` enables counters; `latest` additionally enables
  the queue policy. Both remain opt-in pending GPU/source validation. The helper
  uses the existing support-binary artifact; an older helper fails closed to
  unchanged native behavior.
- The isolated screenshare fixture now embeds complementary CRC-checked frame
  markers and reports unique delivery, duplicates, corruption, dimensions,
  cadence and update-to-receiver delay. It rejects delivery-only success when
  timing markers do not validate.
- A private PipeWire producer and test portal feed synthetic frames directly
  into the real native Linux capturer, encoder and local receiver. They do not
  connect to the host display/audio/portal. The default fixture has no GPU device.
  Window/monitor selections here test portal routing to the generated source;
  they do not reproduce a compositor's actual window/monitor render costs.
- A test-only GPU producer (`tests/screenshare/gpu-source.c`) renders the same
  moving pattern and timing markers into reusable GBM/EGL DMA-BUFs. It permits
  one render in flight and queues the buffer after an asynchronously watched
  native fence signals. It performs no CPU pixel mapping/readback or `glFinish`.
  It offers only single-plane, explicitly linear BGRA and fails on unsupported
  negotiation instead of silently using CPU buffers. This is an interoperability
  fixture, not a replacement capture backend or a production modifier policy.
  It has been compiled, and both GLSL shaders passed offline `glslangValidator`;
  hardware validation has now exercised native DMA-BUF import and delivery on
  the local AMD GPU. The output-dimension/fallback issue below prevents calling
  that path efficient or end-to-end zero-copy.
- `scripts/benchmark_screenshare.py` records input hashes in a comparison plan
  by default. `--run` is required for execution. The hardware plan covers
  1080p30/60, 60/240 Hz source updates, memfd/DMA-BUF and observe/latest modes,
  with three repetitions and alternating matched-mode order. It retains raw
  encoder/bitrate samples and receiver timing; failed validation stops execution
  and prevents an aggregate from omitting that failed run. It does not generate
  CPU/GPU pressure or measure a real compositor's capture cost.

The DMA-BUF fixture follows [PipeWire's buffer ownership and modifier contract](https://docs.pipewire.org/1.4/page_dma_buf.html)
and [EGL native-fence completion/FD ownership](https://registry.khronos.org/EGL/extensions/ANDROID/EGL_ANDROID_native_fence_sync.txt).
Its acquire completion precedes PipeWire delivery; consumer release still relies
on native PipeWire ownership and implicit DMA-BUF synchronization. Explicit
SyncTimeline negotiation and hardware lifetime tests remain outstanding. The
capture counters do not call zero-size DMA-BUF chunks empty: PipeWire permits
them. However, static inspection confirms that both reviewed native callbacks
drop them before dispatching by memory type. The queue policy therefore retains
FIFO for these buffers, avoiding replacement of a usable older frame by a frame
native will discard. Niri's producer already sets the size to 1 for compatibility.
The regression and disassembly are retained in `capture-check.c` and
`out/capture-implementation-20261102/native-zero-dmabuf.txt`.

## Direct Wayland protocol prototype

`integration/video/wayland-capture.c` now implements the client side of
`ext-image-copy-capture-v1` for an explicitly named output. It accepts a dedicated
Wayland connection supplied by its caller; it neither connects to the host nor
chooses a source implicitly. Unsupported output names/protocols fail without
falling back to a different monitor. Isolated window capture remains on the
portal route.

The adapter accepts caller-owned Wayland buffers, validates their generation,
dimensions and advertised SHM format or DMA-BUF format/modifier, and permits
one outstanding frame. It retains presentation timestamps, transform and damage;
it requests full destination damage because recycled buffers can have different
ages. Constraint changes invalidate old generations for new submissions while
allowing an already submitted old-generation frame to finish. Cancellation,
output removal, malformed metadata and constraint failures prevent successful
delivery. An aborted outstanding buffer must be retired after connection teardown,
not immediately reused as a captured image. Downstream encoder release remains
the producer's responsibility.

`tests/screenshare/wayland-check.c` exercises the actual Wayland wire protocol
over a private socketpair, with a synthetic SHM-writing server and ASan/UBSan.
It checks exact output selection even when another monitor is advertised first,
pixel delivery, bounded requests, changes during capture, timestamp/transform
metadata, rejection of stale buffers, session cancellation, teardown of a pending
frame, output removal and malformed events. No host Wayland socket or GPU device
is opened. `scripts/build_wayland_capture.py` builds the shared adapter and
verifies the pinned protocol XML hashes before generating bindings. The XMLs
retain upstream MIT notices and import provenance.

This is a protocol adapter, **not a shipped Discord backend**. Its GPU allocation,
DRM device matching, transform/color conversion, source selection and scoped
PipeWire handoff still need integration and hardware validation. The library
must not be described as having demonstrated faster capture. There is no new
settings toggle or default-backend change.

## vkcapture producer pacing prototype

`patches/capture/vkcapture-copy-rate.patch` adds an optional copy-rate cap to the
reviewed obs-vkcapture revision `8a2c43295fae2ede8ffd382a1581281ceb6153c0`.
`scripts/stage_vkcapture.py` verifies its source archive, applies the patch into a
fresh directory and can compile the Vulkan/OpenGL hooks with one build job.
The archive URL/checksum, GPL-2.0-or-later license and original copyright notices
are retained. No hook was installed, loaded into a game or registered with Vulkan.

The gate runs immediately before `gl_shtex_capture`/`vk_shtex_capture`, after
upstream source/resize checks. `DPE_VKCAPTURE_MAX_FPS` accepts 1–240; absent or
invalid values retain unlimited upstream capture. It avoids sleeping or blocking
the game's present call. Deadlines retain phase, missed deadlines do not accumulate
copy credits, and a restarted/reconfigured capture resets the limiter. Contending
present calls skip capture instead of waiting for the pacing state. The original
presentation, source identity checks and swapchain resize handling are preserved.

The standalone limiter has ASan/UBSan tests using a synthetic clock. A separate
test links the **patched upstream `capture.c`**, replaces only its clock and
checks accepted/invalid settings, cadence, stream reset and missed deadlines.
It does not call socket setup, texture import, graphics APIs or presentation.
Both full hook libraries also compiled successfully. These checks prove the
pacing decisions and build compatibility, not lower GPU utilization or improved
game/viewer frame times.

Artifacts are under `out/capture-implementation-20261102/vkcapture-candidate/`,
with `vkcapture-candidate.json` recording hashes and validation limits. This is
a fixed maximum copy rate, not Discord-controlled demand. A DPE game consumer,
per-frame readiness/release ownership, source selection and dynamic capture-rate
feedback are still missing; the patch is not a complete vkcapture backend.

Capture timing is not GPU-completion timing: the histogram measures the callback
processing interval up to its buffer release call. Source timestamps may be
absent, so receiver latency uses synthetic image markers instead. The private
PipeWire producer embeds truncated CLOCK_MONOTONIC milliseconds; the receiver
uses the same clock domain and rejects stale/invalid markers. No frame pixels
are needed in reports.

## Evidence before hardware authorization

These earlier local checks ran at nice 19 with affinity to CPU 18; threaded math libraries
were limited to one thread. These are functional checks, not host-load tests.
Outputs are under `out/capture-implementation-20261102/`.

| Check                                           | Result and scope                                                                                                                |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| ASan/UBSan native queue fixture                 | Pass: full-frame supersession, conservative metadata, FIFO restoration, bounded scan and return failure                         |
| Native call/return ABI fixture                  | Pass: actual indirect-call replacement, register return layout, observe/latest modes, idempotence and checksum rejection        |
| X11 marker delivery                             | `marker-delivery-fixed`: 117 unique frames, no invalid markers, actual native VP8 sender/receiver; timing instrumentation works |
| Native PipeWire observe                         | `pw-observe-5`: 119 memfd capture callbacks, 118 valid receiver frames, no invalid markers; actual callback hooks exercised     |
| Native PipeWire latest with whole-client pause  | `pw-latest-metadata`: successful delivery/recovery, no return errors; maximum one dequeued frame                                |
| Native PipeWire latest with callback-only pause | `pw-latest-callback`: successful delivery/recovery, no return errors; maximum one dequeued frame, no skipped frames             |

The final `pw-final` check used the pinned prepared native module and stricter
validation: 116 memfd callbacks, 112 unique receiver frames, 100% valid markers
and no return errors. The private callback pause recovered, with no queue
supersession. The GPU producer builds with warnings as errors, and its vertex
and fragment shaders passed offline validation; GPU runtime validation is pending.

The pause checks sleep or suspend only the private fixture, without generating
host CPU/GPU pressure. The callback-delay helper is compiled from a test-only
source; the packaged helper has no delay control.

The queue hypothesis is **not confirmed by these native functional checks**:
they never accumulated multiple dequeueable buffers, even during the bounded
pause. Do not describe the latest-frame mode as a demonstrated performance fix.
The deterministic ownership fixture verifies its behavior if a queue does form.
GPU producer/synchronization behavior still needs its own comparison.

Receiver validation now also requires at least 95% readable, known markers, no
measurement-storage overflow, and the requested input buffer type in exercised
native capture counters. The actual native PipeWire streaming log is required
for PipeWire cases. DMA-BUF input counters alone do not establish zero-copy
encoding; the native pipeline and the CPU-converted test receiver still need
separate measurements. Monotonic timestamp wrap and stale/future rejection have
offline regression coverage.

An early marker check exposed an accidental scrollbar clipping the marker's last
cell; the fixture now scales its synthetic canvas to the viewport and rejects
missing timing evidence. An initial full test run had one packaging-test mock
failure because the mock intercepted the new `pkg-config` invocation. That mock
was narrowed, and the affected test passes. `bun run lint` and `bun run test`
passed after the prototypes: 580 Python tests and 282 JavaScript tests. The native tests include
ASan/UBSan ownership checks, the real call-patch ABI fixture, and software/GPU
producer compilation without opening devices. A formatting-induced GLES header
ordering error was caught by the new compile test and corrected.

The plan-only artifact is
`out/capture-implementation-20261102/hardware-plan-final/plan.json`; no case from
that plan has executed. Its source hashes describe plan creation, so regenerate
a fresh plan after further implementation edits. Final logs are `lint-final.log`
and `tests-final.log` in the same output root.

A fresh candidate is staged under `candidate/`: the corrected native module,
capture metadata, current sidecars and compiled production helper. Its
`staging-check.json` confirms idempotent wrapper preparation and capture
installation before `VoiceEngine.initialize`. The native SHA-256 is
`62812b1287d5ea13fd762e91c18a20532734080cd3b30f0059b538281c0c4f64` and the helper is
`5688c0da4481397b6dc32c8258273d7cd5c08b4b7dc88ed233cb520e1eef9d1e`.
This candidate has not been installed or launched. The earlier `pw-final` run
exercises the same native module in the synthetic harness, not the full wrapper.

The subsequent `candidate-v2/` retains that native module/wrapper and rebuilds
the support helper with the zero-size DMA-BUF FIFO correction. Its helper SHA-256
is `447e3225cde6c766433f529492c976b850d1116856420af7e7f47a82662cc3fc`.
The direct protocol library is built separately under `direct-wayland-prototype/`
and is not enabled in that candidate. Final prototype-check logs are
`tests-prototypes.log` and `lint-prototypes.log`.

## Remaining work at that stage

Continue GPU-buffer lifetime/modifier checks and resolve native encoder fallback
before treating a new producer as efficient.
The direct Wayland protocol adapter must be connected to a producer using that
validated handoff; a vkcapture consumer also needs a safe ownership/copy protocol.
Neither is yet integrated as a Discord backend. Hardware H.264 and synthetic
CPU/GPU load comparisons are now authorized and underway. Real compositor sources,
backend integration and real viewer validation remain outstanding. Do not enable
a new backend or queue policy by default from functional checks alone.

## Hardware validation after session closure

The first hardware run exposed a fixture startup race: native initialization
queues GPU probing asynchronously, and constructing connections immediately
gave them a software-only factory. The harness now awaits `getCodecSurvey` before
creating connections. Its callback supplies one JSON string (which can be
`"null"`); it is a completion barrier, not an error-first callback. The initial
failed runs are retained as `hardware-smoke-01` through `hardware-smoke-03`.

Hardware checks also need a decoder in the isolated receiver. `--openh264` copies
an explicitly supplied trusted library into the fixture and records its SHA-256;
only receiver codec parameters request that decoder. The reused Cisco 2.5.1
library matches `integration/updater/openh264.json`. This does not switch the
sender away from hardware encoding. H.264 software fallback still fails validation.

`hardware-smoke-04` successfully exercised GPU-produced DMA-BUFs, native Vulkan
import, AMD VA-API H.264 and local receiver delivery: 358 captured GPU frames,
349 unique receiver frames, and 100% marker coverage. The actual driver reports
AMD Radeon RX 7900 GRE. The producer waits on its EGL native fence before queueing;
the tested implicit-sync consumer path returned buffers without errors.

However, a requested 1920×1080 GPU source became a 1920×1088 native frame, then
1680×952 encoded video. The logs repeatedly report missing GPU renditions for
that reduced size. Disassembly of `WumpusFrameBuffer::CropAndScale` confirms that
the missing-rendition branch calls WebRTC's CPU crop/scale fallback. This is
retained in `native-crop-fallback.txt`; the GPU report must not be presented as
a successful 1080p or zero-copy performance result. `hardware-memfd-01`, using
the same native module and requested settings with CPU buffers, delivered the
requested 1920×1080. The cause and fix need further native-path validation.

`tests/screenshare/pressure.c` supplies bounded synthetic CPU/GPU contention.
CPU workers inherit the fixture's CPU affinity. GPU work uses a private surfaceless
GLES compute context, a 4 MiB buffer and one dispatch in flight; it captures no
desktop pixels. Each worker rejects durations over 15 seconds. The private
session supervisor starts workers only during the constrained phase, records
exit status, GPU utilization/temperature and utilization of the assigned CPUs,
and reaps workers on success, error or timeout. Both worker completion and native
viewer delivery are required to pass. `gpu-pressure-smoke` passed its bounded
initial/load/recovery check.

`scripts/benchmark_screenshare_load.py` compares explicitly supplied baseline
and candidate modules with identical settings. Each run has 10 seconds before
load, 10 seconds under load and 10 seconds of recovery. Matched module order
alternates between repetitions. Plans record hashes and reject changed inputs
between runs; failed runs remain visible and stop the comparison. These are
synthetic handoff/encode/local-viewer measurements, including receiver CPU
conversion, rather than compositor, internet/SFU or real-game benchmarks.

### Completed HRD comparisons

Two repetitions each of CPU and GPU pressure, with both memfd and DMA-BUF,
completed all 16 runs. Reports are in `load-memfd-60/` and `load-dmabuf-60/` under
the output directory. `load-comparison-summary.json` retains the paired results.
The HRD-corrected module lowered source-to-receiver p95 latency in seven of eight
pairs, but not every pair. Candidate bitrate was generally slightly lower and
QP higher; this does not establish better image quality. Both variants kept
approximately the requested frame rate, so the original severe collapse was
not reproduced. The DMA-BUF runs had the then-unfixed 1680×952 output and must
not be compared directly with 1920×1080 memfd as equivalent capture workloads.

The subsequent [dimension correction](screenshare-native-gpu-20261102.md)
preserves 1080p and removes the demonstrated missing-rendition CPU fallback.

### Early capture pacing

The original latest-buffer optimization did not reduce work for a 240 Hz
producer: `gpu-240hz-30fps-latest` recorded 2,869 callbacks and native processed
frames, `maxDequeued=1`, and no queued older frames to skip. Only 353 unique
frames reached the 30 FPS receiver over 12 seconds. Queue draining alone cannot
solve this high-source-rate cost.

`capture.c` now has a bounded, phase-preserving gate before native conversion.
The connection tracker supplies the active share's requested capture rate;
camera-only connections do not enable it. It stops on share clear, broadcast
stop, native source-ended callbacks and connection destruction. Multiple shares
use the highest known requested rate; an unknown rate disables the global cap.
The user's pacing preference is read at the next source selection. No WebRTC
congestion, CPU adaptation, bitrate or quality limits are disabled.

Only independently consumable full frames are skipped. Cursor-only, explicit
sync and unknown metadata bypass the gate. Skipped frames use the original
native release function exactly once. A one-millisecond maximum early tolerance
avoids halving a correctly paced source due to callback scheduling jitter.
No sleeping, catch-up queue or application presentation-rate limit is introduced.

`paced-240hz-30fps-v2` passed at full 1080p: 2,872 callbacks became 360 native
processed frames, with 2,512 early releases, no return errors and no missing GPU
renditions. The receiver measured 120 unique frames in the four-second GPU-load
phase. This verifies reduced conversion submissions while retaining 30 FPS in
that test; it does not measure total application CPU savings.

The stronger `pacing-combined-60` comparison used the same dimension-corrected
module on both sides, a 240 Hz source, 60 FPS request and four 18-second runs.
Each run had six seconds each before, during and after combined pressure;
the order reversed on the second repetition. All 20 logical CPUs were available
to the fixture and its pressure workers. Median measured CPU use reached 100%
and GPU use 98% in each load phase. Every worker completed and was reaped.

| Run                   | Native processed frames | Early releases | Load p95 latency | Load unique frames |
| --------------------- | ----------------------: | -------------: | ---------------: | -----------------: |
| Observe, repetition 1 |                   4,311 |              0 |            84 ms |                240 |
| Paced, repetition 1   |                   1,080 |          3,226 |            96 ms |                240 |
| Paced, repetition 2   |                   1,071 |          3,190 |           132 ms |                238 |
| Observe, repetition 2 |                   4,314 |              0 |            87 ms |                239 |

Pacing eliminated about three quarters of native conversion submissions, but
**did not improve latency in these two pairs**. Sampling fewer source frames can
add frame-selection delay, and saturation still affects downstream work. The
quality scaler restricted both variants to 40 FPS approximately two seconds
after startup, before the six-second pressure phase; logs identify
`QualityScalerResource` and `max_fps=40`. This is not evidence of a CPU-induced
60-to-40 FPS drop. The media bitrate stayed around 4.9–5.4 Mbps under load rather
than collapsing. Full-resolution delivery and reduced capture work are proven;
a definitive fix for every reported viewer slowdown is not.

`pacing-combined-60/summary.json` contains compact per-run measurements; individual
reports retain timing samples, native stats, source hashes and pressure telemetry.
The pressure worker now supports combined CPU/GPU load, stronger bounded GPU
dispatches, native and supervisor deadlines, and cleanup after partial startup.

### Capture and quality edge checks

Bounded native delivery checks also passed with the corrected DMA-BUF path at
640×360/30, 640×480/15, 1280×720/30, 2560×1440/60, 3840×2160/30 Source,
3440×1440/15 Source and 1280×1024/5 Source. The last case exercises document-style
low cadence. A 1919×1079 Source window becomes 1920×1080 for even chroma dimensions.
The 4K-source/1080p-output case remains entirely at 1080p after the initial GPU
scaler. Every one of these runs reported zero missing-rendition CPU fallbacks.
Reports are `edge-*/report.json` and `large-edge-*/report.json` under the output
directory. These use private portal monitor/window metadata, not the host picker.

The initial large-source attempts failed before native startup because the test
producer still rejected sizes above 1080p. Its explicit bounds now match the
native 4096-pixel maximum; allocation sizes remain bounded. Those failed startup
artifacts were retained rather than presented as application failures.

On the current compiled runtime, native X11 monitor capture with AMD H.264 and
the AV1 guard enabled passed at 1080p30. Static X11 window capture delivered
298 VP8 frames with valid markers over ten seconds, but its strict full-window
640×480 size assertion failed: the captured client area was 638×478. That result
is retained as a geometry limitation, not counted as a fully passing size check.

### Production assembly check

The source changes were assembled through the production portable updater and
native-module preparation paths, using verified cached inputs and the retained
spelling artifact. No Electron rebuild was needed. This newer runtime has SHA-256
`c139aa38e2b0f60253d33fc592dd51ec967eca39d222a42c0d39b5dc5e68d1b9`;
earlier pressure measurements used the independently verified `4223184105a4…`
runtime. Measurements must keep that distinction.

The fresh-profile login/bootstrap check passed, including pinned modules,
OpenH264 preload, native Wayland identity and popout integration. A subsequent
[production settings check](screenshare-settings-20261102.md) verified both
page-world adapters against current Discord factories, native helper loading,
the real settings window and its status IPC. No account login or live call was
performed. The candidate app was closed after each bounded check.

## Earlier staged candidate and checks

The candidate at this milestone was staged at
`/mnt/storage/dpe-screenshare-final-20261102/candidate/app/launch.sh`.
It was assembled through the production updater with verified cached inputs,
using the existing current runtime, without a new Electron compilation.
The accompanying `candidate/profile/` is a fresh signed-out test profile.
The installed launcher and personal profile were not modified at this milestone.
The subsequent explicitly authorized live check below reused the personal profile.

The final support helper has SHA-256
`e9f545554afa194d4d582a357d32fc8a029aef31d0f93850c1023c67b48d0670`.
The app's copy matches the helper used in the final hardware test. Native AV1
now declares a level derived from its actual geometry, rate and session bounds,
and uses normal fallback if later tuning exceeds the emitted level. See the
[native compatibility notes](screenshare-native-gpu-20261102.md).

Final current-runtime media checks under
`/mnt/storage/dpe-screenshare-final-20261102/validation/` passed:

- Actual AV1 hardware receive at 720p30, 1440p60 and 4K30 Source, with full
  dimensions and no decoder errors. Extracted synthetic RTP independently
  confirmed the 720p level declaration. The final helper also passed a
  12-second 720p cadence change from 30 to 10 and back to 30 FPS.
- An 18-second 1080p60 request with a 240 Hz producer and combined CPU/GPU
  pressure recovered from the AMD AV1 size restriction through the real native
  fallback callback to the separately advertised H.264 payload. It retained
  hardware encoding and Electron hardware receiving, full 1920×1080 dimensions,
  and zero missing GPU renditions. Median load reached 100% CPU and 98% GPU.
- That final load run processed 1,080 of 4,312 capture callbacks, releasing
  3,232 before conversion. Load bitrate stayed at 4.91–5.12 Mbps. Its 240 unique
  frames in the six-second load interval reflect upstream quality adaptation
  to about 40 FPS before load began, not a verified sustained 60 FPS result.
- The actual production transport adapter reported method, codec, hardware
  encoder, frame rates, bitrate, counters and native fallback. Stopping the
  share returned its status to idle and disabled pacing. Concurrent-share
  process counters remain unattributable even if the second share stops
  before the next poll.

The final fresh-profile production smoke at
`/mnt/storage/dpe-screenshare-final-20261102/production-check/report.json`
passed welcome/login bootstrap, both current renderer adapter signatures,
native helper loading, the real Screensharing tab and live status IPC. It logged
no capture/encoder installation errors, performed no account login or call,
and closed the app afterward.

`bun run test` passed 609 Python and 304 JavaScript tests. `bun run lint` passed.
Logs are `final-tests.log`, `final-lint.log`, `final-stage.log` and
`final-production-smoke.log` under `out/capture-implementation-20261102/`.
Changed local documentation links were checked. Real settings harness results
are indexed in the [settings notes](screenshare-settings-20261102.md).

Limits at this milestone: no live SFU/viewer or real-game reproduction was performed;
synthetic load is not proof that every reported collapse is fixed. Native
additional GPU renditions can still fall back to CPU scaling. Direct Wayland
and vkcapture work remains a prototype, not a selectable production backend.
Mutter 4563 describes a different synchronization defect and does not justify
replacing this host's niri compositor or forcing shared-memory capture.

## Actual compositor and authorized private-channel verification

The next pass used the current runtime and actual niri monitor-rate capture of
an owned synthetic application window. A narrow Mutter ScreenCast broker selected
only that window; no desktop images, account content or physical camera media
were recorded. The original module reproduced the incorrect 1920×1088 pool,
1680×952 viewer output and missing-GPU-rendition CPU conversion. The corrected
candidate delivered full 1920×1080 with zero such fallbacks under combined CPU/GPU
pressure. Both runs maintained multi-megabit throughput: the severe reported
bitrate collapse did not reproduce even in the original-module baseline.
See the [native notes](screenshare-native-gpu-20261102.md) for exact measurements,
the unequal output-resolution caveat and retained failed fixture attempts.

With the user's explicit permission, the candidate reused the retained
authenticated profile and joined only the designated private quack General
channel. The normal Discord Share Your Screen dialog and actual GNOME portal
selected the owned synthetic window. Discord's SFU reached `RTC_CONNECTED`
and selected AV1 from the advertised native-supported codec intersection.
The real Screensharing tab reported PipeWire and the requested capture cadence.
There was no subscriber; zero outgoing encoded frames and bitrate therefore do
not constitute a live viewer or live hardware-encoding result. Hardware encoding
and receiving were exercised separately in the bounded local media tests.

The check sent no messages and enabled no camera. Cleanup stopped the share,
left the voice channel, confirmed idle diagnostics, restored the saved mute,
deafen and stream-quality preferences, and closed the candidate and owned source.
Normal application activity could update the retained profile/cache; the installed
launcher remains unchanged. Whitelisted evidence and cleanup results are in
`/mnt/storage/dpe-screenshare-final-20261102/live-check/normal-share-flow.json`
and `cleanup.json`. The [codec notes](screenshare-codecs-viewing-20261102.md)
explain the already-enabled Linux AV1 decode path and why no redundant override
was introduced.

## Current candidate: producer negotiation experiment

The refreshed production candidate is
`/mnt/storage/dpe-screenshare-upstream-20261102/candidate/app/launch.sh`.
Its runtime remains the verified `c139aa38e2b0…` build; its support helper is
`3c92eb46f149de278e0bd7ee72f0e323e810e8999ad7abc35d8702a213873eff`.
The actual-compositor test used `9fa3892cd496…`; a final diagnostic guard now
requires the original uncapped maximum before counting a restore acknowledgment,
preventing a delayed capped reply from being mislabeled. Cap publication is
unchanged; the recorded real restoration satisfies the stricter check. The
installed launcher is unchanged.

The normal capture path still uses early frame admission. Producer-side
`maxFramerate` negotiation is implemented but requires `DPE_CAPTURE_UPSTREAM=1`.
It is not enabled by the ordinary Screensharing pacing switch. The experiment
requires reviewed native linkage and an observed variable-rate producer with a
positive maximum, preserves original format/modifier offers, and acknowledges
the cap only after a matching negotiated Format. An owner-loop timer bounds
missing acknowledgments and attempts original offers once. A terminal PipeWire
ERROR cannot be transparently recovered with this API alone; that unresolved
failure path is why the experiment remains off by default.

The real niri test used the same owned 1080p synthetic application and verified
AMD H.264 encoding into Electron hardware decoding. Negotiated producer limits
followed 60→30→60 FPS. Disabling the cap restored 239761/1000 FPS; producer callback
counts confirmed the changes, independently of downstream frame dropping.
There were no negotiation errors, acknowledgment timeouts or codec fallbacks in
that bounded run. This is proof for this producer and these transitions, not
cross-compositor failure-recovery validation or a reproduction of the reported
game-related bitrate collapse. Full evidence and cleanup qualifications are in
the [native notes](screenshare-native-gpu-20261102.md).

The Screensharing capture details now distinguish an actual negotiated producer
limit from a request. Unknown or unattributable limits remain “Not reported”.
Optional producer-helper failures preserve the downstream adapter and native
method return values. Packaging includes the helper, reviewed scope metadata,
sidecar and updater cache invalidation through the normal assembly paths.

`bun run test` passed 611 Python and 311 JavaScript tests; `bun run lint` passed.
The new native ownership/negotiation fixture also passed ASan and UBSan. Logs are
`final-tests.log`, `final-lint.log` and `final-stage.log` under
`/mnt/storage/dpe-screenshare-upstream-20261102/`. The focused real settings
harness is recorded in the [settings notes](screenshare-settings-20261102.md).

The final refreshed-helper production smoke passed with the fresh signed-out
profile: login/bootstrap, native loading, current codec/capture adapter matches,
Screensharing settings IPC and the real tab all worked without adapter errors.
`production-check/report.json` and `final-production-smoke.log` retain the result;
`helper-refresh.json` records the final helper build. The candidate was closed
and no candidate process remained after the check.

The severe original bitrate collapse remains unverified because neither the
original-module real-compositor baseline nor the corrected candidate reproduced
it. No live subscriber/viewer was present in the authorized channel test. Direct
Wayland/vkcapture production integration, transparent producer-cap failure
recovery, and native additional GPU rendition support remain separate open work.

## Initial producer preference follow-up

The new `capture-upstream` initial mode changes only the connect-time offer.
It adds an optional `maxFramerate` range with the desired default, minimum
`0/1`, maximum `UINT32_MAX/1`, and `SPA_POD_PROP_FLAG_DROP`. Existing maximums,
fixed frame rates, unrelated properties, modifiers and native update calls
remain unchanged. An absent peer property disappears during filtering rather
than being mistaken for producer support. The initial mode creates no timer and
never independently calls `pw_stream_update_params`; the previous stricter
runtime experiment remains a separate explicit mode. A versioned N-API export
prevents an older helper from interpreting the new default as its runtime mode.

PipeWire gives a DRIVER producer lower format-selection priority, so the
receiver's default is preferred on a new, unconfigured link. It first tries
fixated defaults, then retries original ranges if no compatible format matches.
This is a preference with compatible fallback, not a guaranteed producer cap.
See [PipeWire link negotiation](https://github.com/PipeWire/pipewire/blob/master/src/pipewire/impl-link.c)
and [SPA filtering and DROP](https://github.com/PipeWire/pipewire/blob/master/spa/include/spa/pod/filter.h).

Current [Mutter](https://github.com/GNOME/mutter/blob/main/src/backends/meta-stream-source.c),
[KWin](https://github.com/KDE/kwin/blob/master/src/plugins/screencast/screencaststream.cpp)
and [niri](https://github.com/niri-wm/niri/blob/main/src/screencasting/pw_utils.rs)
screencast producers use DRIVER and negotiate variable frame rates with a
maximum. Mutter deliberately omits the maximum for a zero-rate source spec;
fixed-rate sources also require preservation because SPA defines maxFramerate
only for variable-rate video. PipeWire update publication is asynchronous, and
the reviewed Discord native ERROR listener exits its capture main loop. Initial
compatible preference avoids that independent runtime-renegotiation failure
path; it does not claim to recover unrelated producer failures.

The reviewed Discord web asset `web.8fb853a63180b897.js` accepts 5, 15, 30 and
60 FPS, including Source resolution. Initial mode therefore prefers at least
60 FPS when a requested rate is known, retaining normal in-place 30→60 changes.
The downstream gate can still admit fewer frames. A missing rate leaves the
native connect offer untouched; `initialWithoutRate` and `lateRateChanges`
expose this ordering limitation. A later native format update can replace the
preference, so diagnostics report the actual observed maximum independently of
`preferredFps`. Unknown, fixed-rate and ended formats clear the maximum.

The focused C fixture exercises real SPA default fixation and compatible
range fallback for fixed maximums, missing maximums, lower producer rates,
fixed frame rates and existing native limits. It includes the reviewed native
framerate range with default 25, minimum 0 and maximum 1000: its initial
fixation fails against a variable-rate producer, then the full-range retry
successfully preserves the maximum preference. It checks no default timer or
independent update call, failed connects and destruction during callbacks.
The fixture passes ASan/UBSan; the existing experimental-runtime ownership tests
and version/mode/rate adapter tests also pass. These are protocol and lifetime
checks, not a measured compositor performance result.

The fresh niri proof is `/mnt/storage/dpe-screenshare-initial-20261103-b/`, with
helper `8df8f9658a16913c8ca1abc73a89f77e21b984047ac451c145a26ea4139a91dc`.
It used the production capture adapter, an owned 1080p synthetic window,
AMD VAAPI H.264 and a local Electron receiver, with no account or real call.
This run did not separately trace the receiver's hardware decoder. Initial
preference was acknowledged at 60/1 FPS and survived modifier
fixation. Requested quality changed 60→30→60 while the negotiated maximum stayed
60, with no independent updates, errors, timeouts or codec fallbacks. Requested
FPS was available before connect in this run; that does not eliminate the
possible ordering race on other source flows.

The preceding control run in `/mnt/storage/dpe-screenshare-initial-20261103/`
left the range offer untouched and negotiated 239761/1000 FPS. At nine seconds
it admitted 449 frames and skipped 1701; the corrected run admitted 448 and
skipped 92. Capture arrivals fell from about 2150 to 540 while preserving the
requested output cadence. This is one quiet synthetic comparison, not proof of
loaded viewer latency or a universal performance gain. Disabling pacing left
the initial producer preference in place until destruction, as expected; the
report does not call it a restoration. Both owned capture sessions stopped and
their source/client processes exited.

Direct image-copy remains a secondary route: its request-driven frames and
damage tracking can bound compositor copy work, but its protocol requires
source-sized buffers and does not remove scaling/conversion or guarantee
zero-copy encoding. The correctly paced PipeWire DMA-BUF path already provides
producer-side admission on these compositors. Vkcapture remains useful for an
explicitly launched game capture path, with Vulkan/OpenGL hooks and separate
buffer handoff; it is not a universal monitor/window replacement. These source
properties do not establish a measured advantage over the corrected native
path. [Image-copy protocol](https://gitlab.freedesktop.org/wayland/wayland-protocols/-/blob/main/staging/ext-image-copy-capture/ext-image-copy-capture-v1.xml),
[vkcapture usage](https://github.com/nowrep/obs-vkcapture#usage).

## Current delivery: native adaptation and compatible producer preferences

The candidate from this adaptation pass is
`/mnt/storage/dpe-screenshare-adaptation-20261102/candidate/app/launch.sh`.
It retains the verified runtime `c139aa38e2b0…` and uses helper
`8df8f9658a16913c8ca1abc73a89f77e21b984047ac451c145a26ea4139a91dc`.
The installed launcher remains unchanged. Native pixel-limit correction and
initial producer preferences are enabled by default; `DPE_CAPTURE_UPSTREAM=0`
disables the producer preference, while `=1` explicitly selects the older
experimental runtime renegotiator. The settings pacing switch still takes effect
at the next source selection. The initial preference allows the full producer
format range and never independently renegotiates a running stream.

The next research pass found a consequential fixture mismatch. Actual fixed
Discord shares use `MAINTAIN_FRAMERATE`, a 600 kbps initial target, a 500 kbps
video minimum and Linux H.264 profile `4d0033`. Earlier fixtures used the native
balanced default and a much larger initial target. The fixture now records and
defaults to the observed production policy; `--transport-profile fixed
--degradation balanced` retains the earlier control. Static defaults and exact
public renderer excerpts are in
`out/codec-research-20261102/production-encoder-fidelity.json`. Viewer wants and
experiments can override production defaults; the controlled maximum remains
6 Mbps and viewer pixel wants remain fixed in these comparisons.

A detailed scrolling-texture source then reproduced a different, concrete
failure: WebRTC requested fewer pixels, Linux capture ignored the maximum and
the quality scaler waited indefinitely for its previous adaptation. Hardware
encoding remained active, but rate limiting discarded most frames. The new
`capture-scale` helper makes the existing GPU scaler honor that maximum and
preserves even dimensions within it. It leaves the normal adaptation mechanism
enabled. The [native investigation](screenshare-native-gpu-20261102.md) records
the exact scoped ABI, independent review and matched pressure evidence. The
synthetic texture is deliberately difficult to compress; it is not a recorded
game or an equal-resolution image-quality comparison.

The final helper passed a 30-second complex-to-quiet recovery check, returning
to full 1920×1080 and 60 FPS. A Source control preserved every 3440×1440 frame
and applied no finite capture limit. Both used actual native encoding/receiving,
valid timing markers and zero missing GPU renditions. The compatible initial
preference separately passed actual niri capture and normal cadence changes.

`bun run test` passed **615 Python and 313 JavaScript tests**. `bun run lint`
passed. Final fresh-profile production loading verified both renderer adapters,
the native helper, real Screensharing tab and idle status IPC without adapter
errors. No account or call was used in this continuation; all owned capture,
pressure and candidate processes exited. Logs, reports, the final helper
manifest and `adaptation-comparison.html` are under
`/mnt/storage/dpe-screenshare-adaptation-20261102/`.

The reproduced adaptation stall is fixed. The exact original severe bitrate
collapse still lacks a reproduction: this stressor kept transport bandwidth
available and primarily collapsed frame delivery. This distinction remains an
open verification item rather than a claim that every bitrate drop is resolved.

## Feedback audit, diagnostic meanings and bounded network recovery

The latest runnable candidate is now
`/mnt/storage/dpe-screenshare-network-20261102/candidate/app/launch.sh`.
It keeps the same verified runtime and native helper as the adaptation candidate;
this continuation adds diagnostic clarity and a test-only network relay, not a
new congestion-control override. The installed launcher remains unchanged.

Native loopback already exchanges real transport-wide congestion feedback.
The prior 10 Mbps estimate was its unconstrained localhost ceiling, not evidence
that feedback was absent. The [codec/viewing audit](screenshare-codecs-viewing-20261102.md)
records the distinction from server experiments and why an own-account preview
cannot stand in for an independent SFU viewer.

Screensharing's Encoding details now shows the last accepted positive configured
video bitrate limit, the native connection-wide bandwidth estimate, and cumulative
rate-limiter, encoder-queue and congestion-window discards. It requests only native
TRANSPORT and OUTBOUND stats (mask 3), retains the explicit privacy whitelist and
leaves unknown values unknown. The native `targetMediaBitrate` field is excluded:
active-stream serialization overwrites it with RTP sender bitrate. Quality-limit
flags are labelled accordingly rather than attributing them uniquely to network
congestion. [Verified native semantics](screenshare-native-gpu-20261102.md#native-statistics-audit-estimate-configured-limit-and-actual-output)
and [settings coverage](screenshare-settings-20261102.md) provide the details.

`--network-profile bottleneck` adds a private sender-video link of 8→2→8 Mbps,
25 ms one-way propagation and at most 100 ms nominal serialization backlog.
Discovery SSRC identifies sender/receiver; arrival order does not. Encrypted
packet bytes remain unchanged, reverse feedback remains unconstrained, and
there is no host network configuration. A hard 256 KiB/2048-packet bound and
owned timer cleanup prevent an unbounded queue. The report separates nominal
serialization delay from observed timer lateness; the network check rejects
lateness above 25 ms. Samples and viewer measurements use the link's phase clock.
Up to 30 seconds uses equal thirds; longer bounded network-only checks retain
10-second initial/constraint phases and spend the remainder on recovery.

The initial run in `network-recovery/` was invalid: a bare Blink timer invoked as
a class method threw `Illegal invocation`, so nothing was delivered. Explicit
renderer-bound timer wrappers fixed the fixture. The corrected 30-second run in
`network-recovery-b/` exercised delivery, shaping and 586 TWCC packets, with
maximum timer lateness 5.194 ms. It failed the strict all-Electron decoder check:
the native low-pixel policy intentionally selected FFmpeg below 640×360. This was
not an error fallback and was not hidden by weakening the check.

The final `network-recovery-45s/` retained the same requested 1080p60 quality,
240 FPS synthetic DMA-BUF source, H.264 hardware sender and fixed viewer wants.
There was no CPU/GPU pressure. Shaping, native transport and marker delivery
checks passed; 883 TWCC packets were observed, maximum timer lateness was
5.394 ms, and queued work was cancelled on shutdown. At 45.2 seconds the
connection estimate was 4.460 Mbps, outgoing video 3.690 Mbps and encoding
60 FPS. The output stayed 530×298 under the deliberately difficult content.
The retained log shows native loss-based recovery holds; no minimum bitrate or
forced resolution was added to bypass them.

This is not an overall hardware-verification pass: besides the intentional
small-frame software decoder, one reconfiguration sample reported an unknown
encoder implementation, and startup capture evidence rotated out of the bounded
native logs. The strict backend/capture assertions remain failed in the retained
report. The test does establish bounded network exercise and resumed delivery;
it does not prove full-resolution recovery, the original high-load game symptom,
or live Discord SFU viewing. `network-summary.json` and `network-recovery.html`
under the new artifact root preserve those distinctions. All test media was
synthetic and all owned test processes exited.

Final checks for this continuation passed **616 Python and 317 JavaScript tests**
and `bun run lint`. The real settings harness passed at
`out/screensharing-settings-20261103-encoding-details/settings.json` (the path's
date is a label; the run was on 2 November). The fresh signed-out production
check passed at the new artifact root's `production-check/report.json`, with both
renderer adapters installed, the native helper loaded, the actual Screensharing
tab and idle IPC present, and no adapter initialization errors. Candidate
transport sidecars match the source hash; runtime and helper hashes remain
`c139aa38e2b0…` and `8df8f9658a16…`. These successes do not replace the failed
strict media assertions described above. No account or live call was used in
this continuation.

## NVIDIA and Intel completion requirement

The user added source-reviewed NVIDIA and Intel compatibility to the final
acceptance criteria while the original game-specific reproduction remains
blocked. Independent [NVIDIA](screenshare-nvidia-20261102.md) and
[Intel](screenshare-intel-20261102.md) reviews compare the current native ABI
and patches with pinned OBS/GPU Screen Recorder sources and vendor documentation.
These are source-based compatibility conclusions, not hardware test results.

The C regression now exercises both Linux NVENC backends (CUDA 0, Vulkan 10)
with the VAAPI AV1 hook installed and affected AMD hardware also present. Native
arguments, bitrate/size changes, suspension and creation failures remain intact;
NVENC is never required to invoke the VAAPI sequence constructor. Intel's
Vulkan/VAAPI path keeps valid 1920×1080 and adaptive 1918×1078 AV1 dimensions,
retains the shared level correction, and does not acquire AMD's padding rule.
The cross-platform legacy Intel type 3 remains unsupported in this Linux binary;
the adapter preserves that native rejection. Synthetic sysfs checks cover
NVIDIA-only and mixed AMD/NVIDIA/Intel hosts.

No new production encoder override was justified by the review. In particular,
native capability/device discovery, negotiated codec intersection, rate
reconfiguration and GPU ownership/synchronization remain authoritative. OBS and
GSR recording presets and copy paths do not establish a lower-latency Discord
replacement. The apparent Intel AV1 level-31 API discrepancy was checked against
the actual driver, which explicitly permits 31; no unnecessary fallback was
introduced. Normal supported share modes continue to choose bounded levels.

Keep these vendor boundaries in the final goal review. Hardware follow-up still
needs each vendor's capability, hybrid-GPU, resize and synchronization cases;
the available AMD run cannot certify NVIDIA or Intel performance. This audit
does not change the outstanding original-game and independent-viewer evidence
gap. The runnable candidate above remains current because this pass changes
only regression coverage and investigation notes.

## Actual Minecraft reproduction follow-up

The user identified the running `oneclient-1.8.9-ornithe-multimc` instance and
then confirmed the affected high-FPS scene. Two additional bounded window-only
checks exercised original and corrected native pipelines. The
[Minecraft investigation](screenshare-minecraft-20261102.md) records the exact
scope, logs and limitations. The severe sustained bitrate collapse did not
reproduce. A brief candidate cadence drop matches native overshoot-control
logs; differing gameplay and geometry prevent a controlled regression claim.
Capture size preservation and the 60 FPS producer preference work in the real
game window. No production override or installed-app replacement was made.

A standalone bounded OpenGL submission mode extends the pressure fixture for
future focused research. It is not a game or compositor simulation. The current
runnable candidate above remains unchanged, and the independent server/viewer
reproduction gap remains open.
