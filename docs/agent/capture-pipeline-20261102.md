# Capture pipeline comparison and lifecycle audit — 2026-11-02

The alternative sources preserve the existing Discord native capture, adaptation,
encoding, transport and receiving stages. They are not replacements for the whole
pipeline. GPU-only transfer avoids CPU pixel readback; it does not establish fewer
GPU copies, lower latency or better performance than the existing DMA-BUF portal.
Earlier producer-only and native-picker-only checks did not test these complete
paths together. The full-path measurements below are required before recommending
one method on performance grounds.

## Copy and synchronization map

These are application-visible operations, not counts of hidden driver copies.
The native common tail imports RGB DMA-BUFs into Vulkan, scales/converts into its
NV12 surface pool and passes GPU surfaces to the selected hardware encoder. An
I420 staging pool being allocated does not prove that every frame was read back;
use actual CPU rendition-fallback counters. Preview/receiver CPU conversion is a
separate cost and must be held constant across measurements.

| Source              | Work before the native common tail                                                                                                              | Completion and lifetime                                                                                                                                                                           |
| ------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Current niri portal | Compositor renders the selected window/output into the PipeWire capture buffer. No DPE intermediate BGRA copy.                                  | PipeWire buffer ownership and the producer's negotiated synchronization. Check actual DMA-BUF/SHM and explicit-sync metadata.                                                                     |
| DPE direct Wayland  | Compositor captures into DPE's GBM buffer, which is the same allocation exported to PipeWire. No DPE pixel blit between these APIs.             | One capture request in flight; wait for compositor `ready`, then queue to PipeWire; reuse after the consumer returns the buffer.                                                                  |
| DPE game hook       | First copy: game's framebuffer/image into hook export texture. Second copy: imported export texture into DPE's owned linear BGRA PipeWire pool. | Hook admission occurs before its copy. An exact generation/sequence lease prevents overwrite; helper ACK follows completion of its copy. The owned pool decouples game storage from the consumer. |

The [niri screencast implementation][niri-cast] renders capture elements through
`dequeue_buffer_and_render`. Its [direct image-copy implementation][niri-copy]
waits an exported render sync point before success. The [protocol's `ready`
event][image-copy] makes the captured buffer available for reading; it permits
waiting for damage, so a static source need not deliver at its nominal FPS.

The game route can avoid a separate compositor capture render and can keep
sampling independent of display presentation. Its extra owned-pool copy and
cross-process completion add costs. The direct route can limit compositor capture
requests before rendering, but still asks the compositor to render/copy the
source and adds a helper/PipeWire boundary. Which tradeoff wins is a measurement,
not a property of the method name. See the [game implementation note][game-note]
and [direct implementation note][direct-note] for format and device restrictions.

### What GSR and OBS actually do

GPU Screen Recorder's reviewed `099a55b` snapshot exports the encoder's VAAPI
NV12/P010 surface with `vaExportSurfaceHandle`, imports its separate planes as
EGL/GL textures, and renders color conversion into those target textures. The
portal capture path imports the source and calls `gsr_color_conversion_draw`.
This avoids an extra intermediate BGRA helper pool in that path, but still does
GPU color conversion/rendering. Its recorder controls both capture and encoder
surface allocation, unlike the current source-only DPE bridge. This is a useful
future optimization direction, not evidence that merely using its capture
protocol reproduces its resource usage. Source: [pinned upstream snapshot][gsr],
`src/encoder/video/vaapi.c`, `src/capture/portal.c`, `src/color_conversion.c` and
`src/recorder/recorder.c`. Retained SHA-256:
`699ee6af0530e01d446c25e1e1372e21d97502f5484c9bd6a09a665c91024483`.
The upstream tree endpoint was unavailable during this pass; the exact retained
snapshot and source manifest were inspected locally.

[OBS PipeWire][obs-pw] imports DMA-BUFs as textures, handles explicit acquire and
release timelines, and removes failed modifiers before renegotiating. Its
[VAAPI texture encoder][obs-vaapi] exports VA surfaces, copies the supplied
texture planes into them using `gs_copy_texture`, then flushes before encode.
The [original obs-vkcapture OpenGL hook][obs-hook] blits the backbuffer into the
exported capture texture. These implementations demonstrate GPU transfer and
ownership patterns; none establishes that all of their paths have no copies.

## Native lifecycle evidence

Reviewed native SHA:
`72096efc87a407b975e9913477d387e2de9a1f1028575c1f97a22365bacb007c`.

- `LinuxVideoSource::UpdateCapturerLocked`, `0x4bc780`, calls
  `cc_linux_capture_update` at `0x4bc8d4`; ordinary quality/FPS updates do not
  destroy and recreate its C capture object.
- Rust `LinuxCapturer::update`, `0xe59ed0`, sends an options message through its
  channel. The existing bridge's `capture_destroy` notification must not be
  reclassified as a harmless quality change based on assumption.
- In the two reported 16:10/16:11 failures, tiled AMD DMA-BUF import succeeded
  and the native GPU surface pool was created. Later the stream changed from
  Streaming to Paused, then reported `no more input formats`. The native thread
  terminated and `Destroying Linux capturer` followed. Destruction was an effect
  of the PipeWire error in these logs.
- `trigger_memfd_fallback`, `0xe8e4e0`, logs a warning before tearing down GPU
  state and republishing memory-buffer offers. That warning is absent from both
  failed sessions while INFO capture logging is present. The evidence does not
  support DMA-BUF import failure as their cause.
- PipeWire 1.6.10's [link format negotiation][pw-link] emits the reported error
  after exhausting format enumeration. It is not an encoder/bitrate error.
  Producer format updates and asynchronous allocation retirement therefore need
  correlation with the failure before attributing a specific cause.

The bridge was left unchanged in this audit. A higher-level source replacement
could still destroy/recreate an object, but it was not established as the cause
of these failures. Preserve source/helper shutdown on actual native capture end.

## Bounded shared-content workload

[Source][workload] and [offline checks][workload-tests] create only their own SDL3
OpenGL window. The source draws no private content and performs no pixel readback
or image saving. Title: `DPE capture pipeline benchmark`; application ID:
`org.dpe.CapturePipelineBenchmark`.

```sh
cc -O2 -Wall -Wextra -Werror tests/screenshare/pipeline-workload.c \
  -o out/capture-pipeline-20261102/source $(pkg-config --cflags --libs sdl3 gl)
# Set the owned nested compositor's environment before launching.
out/capture-pipeline-20261102/source 60 240 1280 720 tiles --fullscreen
# Alternative: uncapped rendering and more complex scrolling content.
out/capture-pipeline-20261102/source 60 uncapped 1280 720 scroll --fullscreen
```

Duration is limited to 90 seconds, with an independent process alarm. At most two
owned draw fences are pending; CPU polling uses bounded waits. It reports actual
submitted FPS, observed draw completions, CPU time, swap wall time and actual
pixel dimensions. These are not GPU execution-time measurements. Motion uses
elapsed time rather than submitted frame count, preserving content speed across
60/120/240 FPS and uncapped runs.

The top two rows use the existing frame marker CRC and the shared
`CLOCK_MONOTONIC` millisecond timestamp modulo 24 bits. The receiver can use
`FrameMeasurements` with its monotonic source clock. Above 1000 source FPS,
multiple draws can share a marker; capture at 30/60 FPS still has adequate marker
resolution, but the marker is not a unique counter for every uncapped draw.
Actual submitted frames are counted separately. Marker latency starts before the
source draw and includes compositor/capture, encoder, transport, decoder and
receiver output conversion; it excludes physical monitor presentation.

## Measurement contract

Use one owned, fullscreen source for comparable content: portal monitor capture, direct capture of the same output, and game hook
of the same application. Confirm all three use the same actual pixel size and
memory path. The reviewed [niri Winit backend][niri-winit] can create a GBM device,
but initialization failure falls back to SHM in its portal path; record negotiated
DMA-BUF/SHM rather than assuming nested operation matches the host.

Keep codec, visible dimensions, requested FPS, bitrate limits, degradation
preference, renderer/receiver and CPU affinity equal. Record output adaptation
rather than comparing lower-resolution throughput as equivalent quality. Require
real encoded and decoded frames; an empty live voice channel with zero viewers
and zero encoded frames is not a throughput test.

Include a 60 FPS source at a 60 FPS share: a hook that observes completion only
on a later present and allows one outstanding lease may lose cadence even when
240 FPS input looks healthy. Measure this rather than inferring game performance
from a high-refresh-only check.

Capture steady-state delivery and a 30→60→30 quality transition, lasting beyond
the selected-source startup timeout. Log source/producer frames, native processed
frames, unique decoded frames, CPU rendition fallbacks, actual dimensions,
marker p50/p95/p99, process CPU, GPU busy and source/helper stop reasons. Separate
configured bitrate, controller bandwidth estimate and actual media bitrate.
Correlate PipeWire format/size/modifier changes and buffer retirement with any
stall. Avoid adding intrusive GPU timing solely to explain coarse CPU/GPU load.

## Complete-pipeline results

The complete native paths now pass, using the owned fullscreen GL workload on
DP-2, 1920×1080 at 240 submitted FPS, 1080p60 Source quality, AMD H.264 VA-API,
a 6 Mbps maximum, and the same native/Electron receiver. The portal baseline
uses niri's Mutter ScreenCast producer (the backend used by its portal), exposed
through the private fixture portal. It does not time the interactive picker.
Custom producers use private PipeWire services and the production native picker
bridge. Network namespaces contain only loopback; no Discord account, SFU,
external viewer or saved pixels were involved.

A nested compositor was considered but rejected for this comparison: niri Winit
reports Flipped180, which the direct backend truthfully does not support. The
owned fullscreen host workload instead gives all three routes the same visible
content and dimensions. No existing application's settings or process were changed.

Final comparable 20-second runs, under
`/mnt/storage/dpe-capture-pipeline-20261102/`:

| Method             | Run                   | Unique decoded frames | Marker median / p95 | Native CPU, one core | Helper CPU, one core |
| ------------------ | --------------------- | --------------------: | ------------------: | -------------------: | -------------------: |
| Portal backend     | `portal-240-measured` |                 1,187 |          51 / 55 ms |                56.4% |                    — |
| Direct Wayland     | `direct-240-measured` |                 1,191 |          59 / 62 ms |                56.6% |                0.25% |
| Game capture (EGL) | `game-240-source`     |                 1,189 |          56 / 58 ms |                57.7% |                0.95% |

All runs kept 1920×1080, used DMA-BUF import, and recorded zero native CPU
rendition fallbacks. All delivered approximately 60 unique frames/s after startup.
An earlier matched portal/direct pair measured 53/59 and 60/65 ms respectively,
consistent with the ordering, but these are short exploratory measurements, not
statistical proof across hardware or workloads.

CPU uses `/proc` process/thread descendants over approximately seconds 2–18;
100% means one CPU core. The native total includes the local receiver and its
CPU output conversion for marker analysis. It excludes the host compositor and
other running applications. Per-process DRM engine counters also show similar
native graphics/video work; the game helper additionally spends about 0.34% of
a graphics engine on its intermediate copy. These are not total-system resource
savings. Initial resource samples that omitted subprocesses spawned by secondary
threads were discarded; only the named runs above use the corrected sampler.

The 60 FPS EGL workload also passes the full pipeline (`game-60-source`): 1,188
unique decoded frames in 20 seconds, median 58 ms and p95 60 ms. The matched
producer-only old/new handoff test demonstrated the actual improvement: 30 FPS
with next-present polling, versus 60 FPS with exported GPU completion fences.
See the [game evidence][game-note] for exact snapshots and the GLX limitation.

The uncapped direct run (`direct-uncapped-rate`) submitted approximately 11,204
source frames/s while changing producer limits 60→30→60→15→60. It passed all
14 checks, preserved 1080p, resumed normal unique-frame cadence, and retained the
local 10 Mbps bandwidth estimate. Encoder FPS alone can include repeated frames;
use marker uniqueness when judging the intentional 15 FPS interval. This simple
shader is not Minecraft or a saturated 3D workload, and it does not establish a
fix for the previously unreproduced severe internet-viewer bitrate collapse.

`game-av1-720p30` verifies the same game path with actual AMD hardware AV1,
GPU scaling from 1080p to 720p and a 30 FPS requested cap. The 1080p comparisons
use H.264 because the existing VCN4 AV1 coded-size guard intentionally rejects
that problematic geometry. No encoder guard was disabled to make a benchmark pass.

The uncapped EGL game run (`game-uncapped-source`) submitted approximately
11,440 frames/s and delivered 892 unique decoded frames in 15 seconds, with a
56 ms median and 58 ms p95. It kept 1080p and had no native CPU rendition fallback.
These uncapped shader runs demonstrate bounded capture admission at very high
submission rates, not equivalent Minecraft GPU complexity.

### Vulkan receive-startup limit

Both Vulkan checks completed without helper termination and used real exported
completion fences. The first (`vulkan-60-source`) had a 2,964.1 ms receiver gap
after its initial frames. At 1/2/3 seconds the sender had encoded 53/114/174 frames
while the receiver had decoded only two. Primary-SSRC packet counts and payload
bytes matched exactly, with no packet loss, NACK/PLI requests or decode errors;
delivery resumed at the next three-second IDR. Native teardown reported 179
receiver drops. This places the stall before decode, but without recorded RTP it
does not identify native assembly/reference tracking versus encoded packetization.

The separate eight-second repeat (`vulkan-60-repeat`) delivered 472 unique frames,
with a 20.2 ms maximum interval and normal 60 FPS delivery after startup. Its clean
result does not erase the first fault. The first startup fault remains unresolved;
do not summarize the coarse passed validation flag as proof of uninterrupted
Vulkan playback. This clear-rectangle fixture is also not a performance-equivalent
replacement for the GL shader used in the comparison table.

Current upstream WebRTC's [H.264 reference selection][reference-finder],
[sequence continuity handling][sequence-finder] and [packet buffer][packet-buffer]
explain mechanisms that can wait for the next keyframe without decode errors.
They are research context, not proof that this proprietary native build takes
exactly that failing path.

## Shared controller stop and replacement fixes

The common controller previously treated every main-frame `did-start-navigation`
as a new document. Electron emits that event for History API route changes too.
The retained Electron runtime reproduced this with two actual same-document
navigations: the original controller stopped its owned helper once, while the
corrected controller stopped it zero times. Both stopped it once on the subsequent
real document navigation. Reports: `navigation-old-c/report.json` and
`navigation-fixed-c/report.json` under the artifact root. This is a confirmed
stop trigger affecting both custom methods; it does not retrospectively prove
which route event preceded every reported live-session failure.

The controller now handles both current event details and legacy positional
arguments, preserves active capture when another picker opens/cancels, and retires
a direct source after its replacement actually starts. The single-child game
broker commits replacement only after an explicit valid game choice and waits for
old-child shutdown. Additional regressions cover cancelling a successful startup,
release of the ownership lock, and a helper that emits ready then terminates in
the same event turn. Such a dead node must never be returned as a valid selection.

Direct producer fixes include recoverable protocol failures, constraint updates,
retained old-buffer ownership, stable modifiers for rate updates, initialized
optional SPA fields and a timer grant that could remain stuck across a format
generation change. Game fixes include asynchronous pool retirement, repeated
texture generations, rate negotiation and real exported completion fences.
Independent reviews found and checked the additional lifetime/failure-path fixes.

The recommendation for this machine remains **portal by default**. Direct
Wayland is an alternative when the portal fails, not a measured speed upgrade.
Game capture can be useful for application isolation or compositor-specific
problems, but the implemented path has another RGB copy and did not reduce
latency or measured native CPU in this healthy-portal comparison. EGL completion
fences remove a concrete 60 FPS handoff bottleneck; GLX/no-export fallback still
has the older timing limitation. No automatic preference was changed.

[niri-cast]: https://github.com/urayde/niri/blob/3af9296f088547de86c4ad19f9b293d94dea49b5/src/screencasting/mod.rs
[niri-copy]: https://github.com/urayde/niri/blob/3af9296f088547de86c4ad19f9b293d94dea49b5/src/handlers/image_copy_capture.rs
[niri-winit]: https://github.com/urayde/niri/blob/3af9296f088547de86c4ad19f9b293d94dea49b5/src/backend/winit.rs
[image-copy]: https://gitlab.freedesktop.org/wayland/wayland-protocols/-/blob/main/staging/ext-image-copy-capture/ext-image-copy-capture-v1.xml
[gsr]: https://dec05eba.com/snapshot/gpu-screen-recorder.git.r1536.099a55b.tar.gz
[obs-pw]: https://github.com/obsproject/obs-studio/blob/65c320b46e1af5c7a555161aea6182cdf7a8c524/plugins/linux-pipewire/pipewire.c
[obs-vaapi]: https://github.com/obsproject/obs-studio/blob/65c320b46e1af5c7a555161aea6182cdf7a8c524/plugins/obs-ffmpeg/obs-ffmpeg-vaapi.c
[obs-hook]: https://github.com/nowrep/obs-vkcapture/blob/8a2c43295fae2ede8ffd382a1581281ceb6153c0/src/glinject.c
[pw-link]: https://github.com/PipeWire/pipewire/blob/1.6.10/src/pipewire/impl-link.c
[game-note]: vkcapture-backend-20261102.md
[direct-note]: direct-wayland-backend-20261102.md
[workload]: ../../tests/screenshare/pipeline-workload.c
[workload-tests]: ../../tests/test_capture_pipeline_workload.py

## Validation and staged update

`bun run lint` and `bun run test` pass: 634 Python and 339 JavaScript tests.
This includes actual synthetic Wayland protocol retries, ASan/UBSan buffer-lifetime
checks, delayed/failed sync-file checks and controller cancellation regressions.
Reports and bounded source-only fixtures remain under the artifact root; the
matched chart and raw summary are `comparison.html` and `comparison.json`.

The independent capture build is `capture-final/`; `candidate/launch-test.sh`
selects a separately staged app and the existing test-candidate profile after
Discord is closed. It has not been launched against that profile. The running
Discord and Minecraft processes, existing profile and installed application were
left untouched. A Discord restart is needed for the controller fix; games need
the updated opt-in wrapper on their next launch for exported-fence handling.
Older already-loaded hooks remain compatible with the corrected producer.

The staged runtime is byte-identical to the retained verified Electron, SHA-256
`c139aa38e2b0f60253d33fc592dd51ec967eca39d222a42c0d39b5dc5e68d1b9`.
No Electron compilation ran. The final direct producer is `d545dcd50bba…`; the
game producer is `2c4b76de2c2b…`, matching the tested binaries. Full bundle and
controller hashes are in `candidate/capture-update.json`; packaged exact GPL
source and checksum validation are retained.

[reference-finder]: https://github.com/webrtc-mirror/webrtc/blob/main/modules/video_coding/rtp_frame_reference_finder.cc
[sequence-finder]: https://github.com/webrtc-mirror/webrtc/blob/main/modules/video_coding/rtp_seq_num_only_ref_finder.cc
[packet-buffer]: https://github.com/webrtc-mirror/webrtc/blob/main/modules/video_coding/packet_buffer.cc
