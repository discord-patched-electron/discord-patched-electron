# Linux capture methods and implementation priorities, 2 November 2026

**Recommendation:** keep portal/PipeWire as the general desktop and window
backend. First establish frame-age telemetry and a verified GPU handoff into
Discord's native encoder. Apply GPU Screen Recorder's surface-sharing and
conversion ideas where the current path demonstrably adds work. Then prototype
direct Wayland image-copy capture for monitors and vkcapture for games. Reserve
KMS for cases where measurements implicate compositor capture.

This is a source and static-binary research result, **not a measured performance
ranking**. No Discord launch, capture session, portal picker, GPU capability
probe, benchmark or load test was started. No installed backend, driver,
compositor configuration or running Discord process was changed. Research
artifacts are under `out/capture-research-20261102/`; local native inspection ran
at low priority on one CPU.

The earlier [screenshare investigation](screenshare-performance-20261101.md)
records the H.264 HRD correction and optional diagnostic-copy reduction already
integrated into repository build/updater preparation. Neither establishes a
viewer performance improvement yet. This pass adds research and a concrete
implementation sequence; it does not install an alternative capture backend.

## Scope and local evidence

The user sees bitrate/quality drops and viewer-visible slowdown with both
monitor and application shares at 1080p30/60 under motion or CPU/GPU load. Local
preview behavior is unknown. Earlier passive logs show approximately 60 capture
inputs per second alongside bitrate-driven drops and quality adaptation. That
does not establish low capture cost, even frame delivery, low GPU contention,
or successful viewer delivery. It also does not clear the network.

Read-only package/device inspection found:

| Component             | Observed installation                                                         |
| --------------------- | ----------------------------------------------------------------------------- |
| GPU                   | AMD Navi 31, PCI `1002:744c`; the ID alone does not identify one retail model |
| Compositor            | `niri-tearing-git 26.04.r233.g3af9296-1`, from the `urayde/niri` fork         |
| Mesa / libva / libdrm | `26.2.5-1`, `2.24.1-1.1`, `2.4.134-1.1`                                       |
| PipeWire              | `1.6.10-1`                                                                    |
| Portal                | xdg-desktop-portal `1.22.1-2.1`; GNOME `51.0-1.1`, GTK `1.15.3-2.1` backends  |
| Kernel                | `7.3.0-4-cachyos`                                                             |

The package-corresponding niri commit is
`3af9296f088547de86c4ad19f9b293d94dea49b5`. Its source registers
`ext-image-copy-capture-v1` and an output capture source manager, and implements
output/cursor capture. No toplevel capture source manager was found in the
reviewed registration/handler. Thus **direct monitor capture is a realistic
prototype here; isolated window capture through that interface is not established**.
Generic upstream niri compatibility tables are insufficient for this fork.
The package name does not establish that tearing is enabled. [Niri registration][niri-registration],
[capture handler][niri-image-copy].

## Compare the right parts of the pipeline

Portal, DMA-BUF, Vulkan and VAAPI describe different layers:

```text
selected application / compositor output / KMS scanout
    -> capture and producer completion
    -> shared GPU buffer and ownership transfer
    -> crop / scale / cursor / RGB-to-YUV conversion
    -> encoder input surface and encode queue
    -> Discord rate control, packet pacing and transport
    -> viewer jitter buffer, decode and presentation
```

The portal selects and authorizes a source and opens a scoped PipeWire remote;
it is not a D-Bus pixel transfer for every frame. PipeWire can transport DMA-BUF
descriptors. DMA-BUF describes shared memory, not a promise that no GPU copy,
conversion, wait or CPU mapping occurs. Vulkan can process frames or encode
them; those are separate capabilities. VAAPI is an acceleration interface, not
a screen-selection method. [Portal ScreenCast API][portal], [PipeWire DMA-BUF guide][pw-dmabuf].

Minimize **age of the frame reaching the viewer**, CPU cost and disruption to
the game together. FPS alone misses stale frames and bursts. A frame of queue
delay is about 16.7 ms at 60 FPS or 33.3 ms at 30 FPS; these are arithmetic
examples, not measurements here. Likewise, one tightly packed 1080p BGRA stream
at 60 FPS contains about 498 MB/s of pixel data before accounting for read/write
traffic or conversion. Avoiding a CPU round trip can matter even with hardware
encoding enabled.

## Decision matrix

Priorities below are engineering judgments from the reviewed implementations.
None claims an experimentally proven latency advantage on this installation.

| Method                             | What it captures                                               | Useful property                                                       | Remaining cost or constraint                                               | Recommendation                                |
| ---------------------------------- | -------------------------------------------------------------- | --------------------------------------------------------------------- | -------------------------------------------------------------------------- | --------------------------------------------- |
| Portal + PipeWire DMA-BUF          | Windows/monitors offered by the portal                         | Existing selection, GPU sharing, cross-desktop integration            | Compositor rendering, fences, conversion, fallback and queues still matter | Keep default; audit/optimize first            |
| Direct `ext-image-copy-capture-v1` | Sources exposed by compositor; output/cursor in this niri fork | Client-owned buffers, damage and presentation metadata                | Still compositor rendering; source support and native handoff required     | First monitor prototype                       |
| obs-vkcapture Vulkan/OpenGL hook   | An opted-in application's presented frames                     | Can avoid the compositor's capture rendering                          | Extra game-side copy; hook at launch; synchronization and rate limiting    | High-value optional game prototype            |
| GSR-style DRM/KMS                  | Display scanout planes                                         | Can import scanout buffers without an extra compositor capture target | Privileged helper, planes/cursor/VRR/HDR; no isolated window               | Conditional later monitor backend             |
| `wlr-screencopy`                   | Outputs/output regions on supporting compositors               | Existing DMA-BUF implementations                                      | Older protocol, compositor work, availability                              | Compatibility backend, after ext              |
| `wlr-export-dmabuf`                | Output buffers on supporting compositors                       | Exports compositor-owned DMA-BUFs                                     | Availability, lifetime and format constraints                              | Only where actually advertised and beneficial |
| Gamescope PipeWire capture         | A gamescope session                                            | Existing game-oriented producer and GPU formats                       | Extra compositor if gamescope is introduced solely for capture             | Support existing gamescope users              |
| XComposite texture import          | X11 application windows                                        | Can stay on GPU                                                       | X11/XWayland scope and interop                                             | X11 fallback, not a Wayland-wide replacement  |
| XShm / `x11grab` CPU path          | X11 desktop/window pixels                                      | Compatibility                                                         | CPU readback/copy and possible upload                                      | Last-resort compatibility                     |
| NVIDIA Capture SDK / NvFBC         | NVIDIA-supported desktop/app sources                           | Vendor capture integration                                            | NVIDIA hardware/driver requirements                                        | Irrelevant to this AMD host                   |
| Recorder preview / virtual camera  | An intermediate app or video device                            | Easy interoperability in some applications                            | Additional processing; loopback and camera semantics                       | Avoid as the performance architecture         |

The evidence for each row follows; GPU Screen Recorder, wl-screenrec and OBS
are applications containing several of these methods, not single capture APIs.

## Portal/PipeWire: strongest default, with concrete audit targets

The reviewed niri producer renders into negotiated buffers, supports DMA-BUF and
shared-memory paths, and normally exports a render-completion fence before
publishing the frame. Its ordered completion list holds later buffers behind
the first unfinished render. Its default pool is eight buffers, with a permitted
range of two to sixteen; **allocated pool capacity is not measured queue depth**.
The source already preserves cadence phase after missed deadlines, so a new
phase-preserving timer would duplicate existing work. [Niri PipeWire producer][niri-pw].

Two compositor details merit later tracing: pointer-only updates still have a
full-render TODO, and `queue_after_sync` deliberately marks a frame submittable
if a sync point cannot be exported, including a documented failure case. The
latter is a correctness/failure-path audit target, not evidence that it happens
during the reported slowdown. Normal completion waiting should not be removed
to make timing numbers look better. [Producer cursor/render and sync code][niri-pw].

The exact reviewed Discord native module contains separate DMA-BUF and memfd
processing, explicit-sync extraction/negotiation, release signaling, and a
memfd fallback. It would be wrong to propose “add DMA-BUF” or “add explicit sync”
as though these mechanisms were absent. Symbols establish implementation
capability, not the mode selected for every live frame. See the native evidence
section below.

There is a more specific queue hypothesis: the inspected
`capture_linux::process_pipewire_stream` dequeues one buffer, processes it and
returns; its reviewed `FnMut` adapter tail-calls that function once. OBS drains
available buffers before processing the latest one. GSR drains to the latest
buffer containing video while preserving cursor metadata from cursor-only
updates. This makes **stale-buffer processing worth measuring**. It is not yet
proof of native backlog: PipeWire scheduling, buffer counts and downstream
behavior determine whether multiple buffers actually accumulate. [OBS dequeue
implementation][obs-pw], [GSR PipeWire implementation][gsr-pw].

Recommended work: report source timestamp, dequeue age, actual queue depth,
negotiated modifier, buffer type, GPU device and fallback reason; then implement
latest-useful-frame selection where backlog exists. Return skipped buffers with
correct synchronization, preserve cursor/crop/format updates, and never do
GPU conversion on every stale frame simply to discard it later.

For explicit sync, negotiate producer and consumer support together. This niri
source normally waits for render completion before publication; no
`SPA_META_SyncTimeline` producer path was found in the inspected implementation.
Consumer support alone cannot enable a different producer protocol. OBS provides
a useful reference for acquire/release timeline handling, including skipped
buffers. [Niri producer][niri-pw], [OBS synchronization code][obs-pw].

## GPU Screen Recorder: borrow the architecture selectively

Reviewed GSR snapshot: `r1536.099a55b`, taken from the official snapshot URL
listed in its maintained Flathub manifest and verified against the manifest's
SHA-512. This avoided relying on search snippets or inaccessible cgit pages.
[Official snapshot][gsr-archive], [packaging manifest][gsr-manifest].

### Most useful: conversion into encoder-owned GPU surfaces

The VAAPI encoder allocates a hardware frame, exports its surface as DRM PRIME
layers, and imports NV12/P010 planes as EGL textures. This permits GPU conversion
to write into the encoder surface instead of downloading RGB to system memory
and uploading YUV again. The conversion implementation also provides a model
for combining scaling, color conversion and other transforms. [GSR VAAPI
implementation][gsr-vaapi], [GPU conversion][gsr-conversion].

This is the strongest general idea to reuse. First inspect Discord's existing
Vulkan-to-VAAPI handoff: if it already uses an equivalent path, focus on waits,
queues or fallback instead. Prefer an implementation using the existing GPU
API where possible; adding EGL solely to imitate GSR could create another
interop boundary. Encoder input ownership must remain valid until encoding has
finished, not merely until command submission.

### KMS is a separate, conditional capture choice

GSR's helper queries DRM planes/framebuffers and exports their handles as
DMA-BUFs. Its capture code handles monitor layout, cursor and display-specific
details before conversion. This can avoid a separate compositor capture render,
but still needs a correctly completed image and conversion/encoding. Capturing
scanout is not isolated application capture; cropping an output rectangle also
captures overlapping windows. [GSR KMS helper][gsr-kms-helper], [KMS capture][gsr-kms].

Do not equate a minimal `ffmpeg -f kmsgrab` experiment with GSR's complete
handling. FFmpeg documents DRM-master/`CAP_SYS_ADMIN` requirements, selection of
a CRTC/plane, and interval sampling that is not synchronized to framebuffer
changes. Multi-plane composition, cursor, hotplug, transforms, color and VRR
need deliberate treatment. No capabilities or helper were installed in this
research. [FFmpeg kmsgrab documentation][kmsgrab].

KMS becomes worthwhile if later traces show compositor capture is the dominant
cost and direct image-copy does not resolve it. Keep a helper narrow and
separate from Discord, preserve the user's chosen monitor, and treat failure
as a source failure rather than silently widening a window share to a monitor.

### Defaults that should not be copied wholesale

The reviewed GSR codec setup uses `async_depth=3` for VAAPI/Vulkan; this favors
overlap and throughput, but does not prove the lowest interactive latency. The
recorder can repeat frames to satisfy constant-frame-rate output. A Discord
sender should not build a catch-up queue of old frames after overload.
[Codec configuration][gsr-codec], [recorder loop][gsr-recorder].

The Vulkan encoder uses GL/Vulkan semaphore interop when available and a
`glFinish` fallback otherwise. A Vulkan label therefore does not establish an
entirely asynchronous GPU path. The PipeWire code duplicates DMA-BUF FDs and
requeues buffers; copying that pattern requires a separate lifetime and
synchronization review. An open descriptor preserves the allocation, not
exclusive access to unchanged pixels. [GSR Vulkan encoder][gsr-vulkan],
[PipeWire code][gsr-pw], [kernel DMA-BUF synchronization][kernel-dmabuf].

GSR's own performance examples are useful motivation, not measurements against
this Discord implementation. Current OBS also has texture-based VAAPI encoding;
its reviewed implementation performs GPU texture copies into VAAPI-backed
surfaces. Comparisons against an older CPU-upload OBS path would not establish
the current relative cost. [OBS VAAPI texture encoding][obs-vaapi].

## vkcapture: worthwhile for games, with a different cost profile

obs-vkcapture captures an application through a Vulkan layer or OpenGL
injection enabled when the application starts. Its README documents
`obs-gamecapture` and the Vulkan-only `OBS_VKCAPTURE=1` route. It is not a method
for attaching to every already-running Wayland window. The current Discord/game
session need not be changed to research or prepare an adapter. [Project usage][vk-readme].

The reviewed Vulkan hook transitions the presented swapchain image, copies or
blits it into an exported image, submits that work, and changes presentation
semaphore dependencies. It can bypass the compositor capture pass, but adds
work to the application's presentation path. Its capture dispatch is tied to
presentation, and the reviewed control structure has no target-FPS field. A
240 FPS game shared at 60 FPS can therefore trigger unnecessary producer work
unless the producer is throttled before the copy. [Vulkan hook][vk-layer],
[control protocol][vk-protocol].

Proposed adapter requirements:

- Accept an explicitly selected hooked application and support reconnect,
  swapchain recreation, resize and source destruction.
- Limit capture work to demand before copying; do not silently limit game FPS.
- Transfer the real format, modifier, plane offsets/strides, color information
  and device identity. Avoid host-visible or cross-GPU fallback when same-GPU
  import is available. The reviewed hook has different allocation behavior
  for matching and mismatched GPU devices. [Allocation and copy code][vk-layer].
- Establish frame identity, source timestamp, readiness and consumer completion.
  The inspected socket structures do not contain per-frame timestamps,
  sequences or acquire/release fence fields. That does not prove the existing
  OBS path is broken; it means an independent consumer must understand its
  implicit-sync and persistent-texture contract rather than inventing one.
  [Protocol structures][vk-protocol], [OBS consumer][vk-consumer].
- Preserve the existing Vulkan layer/OpenGL chain and test actual native/Proton
  titles later. Compatibility with one game does not establish compatibility
  with all launchers, graphics APIs or anti-cheat configurations.

Use vkcapture as an optional game source after the shared GPU handoff works.
It is a strong candidate when the compositor's capture work is costly, but its
game frame-time impact must be part of the acceptance test.

## Direct Wayland capture and wl-screenrec

`ext-image-copy-capture-v1` negotiates destination-buffer constraints and
provides transform, damage and presentation metadata. It can wait for new
damage, so a stationary desktop need not produce a new image every frame
interval. Implement constraint renegotiation and source stop explicitly; do
not interpret an unchanged image as a hung source. Respect buffer damage
history when rotating a pool. [Protocol text][ext-copy].

This machine's niri fork implements the output/cursor path. Both its direct
capture handler and PipeWire producer use compositor rendering and completion
handling. Removing PipeWire does not inherently remove the expensive GPU work;
the value of a direct prototype is controlled buffer allocation and a useful
comparison of delivery/queue behavior. Window selection remains on the portal
path unless an actual toplevel source is exposed. [Direct handler][niri-image-copy],
[PipeWire producer][niri-pw].

wl-screenrec is a useful compact implementation reference for ext and
`wlr-screencopy`, DMA-BUF import, GPU filtering and hardware encoding. Its
reviewed VAAPI allocation route requires a linear modifier, while its Vulkan
route can use a negotiated modifier list. That is an implementation constraint,
not a reason to force linear buffers or disable compression globally in DPE.
Its filtering code distinguishes GPU scaling from software-output download.
[Allocation and capture dispatch][wl-main], [filters][wl-filter].

One recording-specific choice should not be carried over: wl-screenrec's
optional `--max-fps` limiter deliberately retains a frame to decide which frame
to keep around a VRR gap. This improves a recording decision but adds latency.
An interactive sender should instead select the newest useful ready frame at
its deadline and separately handle static-content refresh requirements.
[FPS limiter][wl-fps].

Use `wlr-screencopy` as a compatibility path where ext is unavailable; its
protocol supports output/region copying and, in applicable versions, DMA-BUF
destinations. `wlr-export-dmabuf` instead exports read-only output buffers with
format/modifier and readiness information. Neither is universally advertised
by Wayland compositors. No need to add both before proving a benefit from one
on the target machine. [Screencopy protocol][wlr-copy], [export protocol][wlr-export].

## Other approaches and encoder choices

**X11:** XComposite texture import can avoid CPU readback; OBS uses a pixmap
texture path. Its XShm implementation reads into shared CPU memory and uploads
the image. Switching Discord to X11/XWayland does not make arbitrary native
Wayland windows available through XComposite. Retain it for actual X11 sources
where useful, not as a universal performance switch. [OBS XComposite][obs-xcomp],
[OBS XShm][obs-xshm].

**Gamescope:** the reviewed gamescope PipeWire producer includes NV12 support;
the Discord binary also contains gamescope capture code. This merits support
when the game already runs in gamescope. Adding another compositor solely for
capture introduces another variable and has no demonstrated advantage here.
[Gamescope producer][gamescope].

**NVIDIA:** Capture SDK 9.0 adds both a PipeWire Wayland backend and direct
Vulkan application capture by PID. Thus “NvFBC is X11-only” is too broad for
the current SDK, though individual applications may implement only its X11
route. The SDK requires supported NVIDIA hardware/drivers; it is not an AMD
solution. NVENC is an encoder API, not a capture method. [NVIDIA Capture SDK][nvfbc].

**Virtual cameras/recapturing previews:** the reviewed v4l2loopback code accepts
MMAP streaming buffers and rejects other memory types in the relevant queue
operations; it does not establish a DMA-BUF zero-copy route. Generic V4L2
capability should not be attributed to every loopback driver. Routing a recorder
through a preview or virtual camera also changes source and potentially audio
semantics. Avoid this as DPE's main performance design. [Reviewed loopback
implementation][v4l2loopback].

**Vulkan Video:** consider it as an encoder/interop workstream after capturing
GPU frames correctly. It can reduce API boundaries when capture processing is
already Vulkan, but uses the hardware's video resources; changing API does not
create extra encoder capacity. Query actual device/profile/format support
later, and integrate image layout transitions, ownership, synchronization,
rate control and keyframe requests. “Vulkan processing present” does not prove
Vulkan encoding is active. [Vulkan encode specification][vulkan-encode].

**VAAPI and codec tuning:** retain the existing hardware path initially to
isolate capture changes. Test latency versus throughput for in-flight frame
depth, rate-control settings, reference/reordering behavior and conversion
surface ownership. A more efficient codec could improve quality per bit only
if the complete Discord/viewer path negotiates it; capture replacement alone
does not unlock codec support. Keep the staged H.264 correction as a separate
experimental variable.

**Sunshine/OBS:** use their implementations as references and later comparison
tools, not drop-in Discord transports. Sunshine documents GPU-load-related
encoding slowdowns and scheduling remedies in its environment. That supports
checking GPU scheduling/headroom, not adding global priority/capability changes
to this host without measurements. [Sunshine troubleshooting][sunshine].

## The Discord handoff is the first implementation gate

Static inspection used a copy of the previously identified native module,
without loading or attaching to it:

```text
out/screenshare-lab/probe/voice/discord_voice.node
SHA-256 f56d3c31611f769e6798bb2bc03ad2b5a21e2b29224825ca740af013e145f0d8
```

Relevant symbols/addresses in that exact binary:

| Evidence                                              | Address    | Meaning and limit                                                       |
| ----------------------------------------------------- | ---------- | ----------------------------------------------------------------------- |
| `capture_linux::process_pipewire_stream`              | `0xe79670` | One dequeue/processing pass in inspected function                       |
| Corresponding `FnMut::call_mut`                       | `0xe868a0` | Tail-calls that pass once; not an observed runtime backlog              |
| `gpu_processing::process_dmabuf_frame`                | `0xe83970` | Native GPU input path exists                                            |
| `cpu_processing::process_memfd_frame`                 | `0xe82c60` | Distinct CPU fallback exists                                            |
| `explicit_sync::extract_from_buffer`                  | `0xe879a0` | Sync extraction exists; live negotiation still unknown                  |
| `create_explicit_sync_buffer_param_bytes`             | `0xe88ea0` | Native explicit-sync negotiation support                                |
| `gpu_processing::signal_release_if_needed`            | `0xe859a0` | Release handling exists                                                 |
| `videoshare::WrapCapturedFrame`                       | `0x450d30` | Separate activity-capture Linux frame conversion                        |
| `VoiceConnectionWrapper::SetDesktopSourceWithOptions` | `0x3029c0` | Reviewed JS source options do not expose a documented DMA-BUF input API |

The Electron integration's `WebFrameMain.captureStream()` and
`electron_video_shared.h` define GPU/shared-memory frame transport for captured
Electron web frames. They are not automatically an external desktop-source
injection API. More critically, the reviewed Linux **activity** capture consumer
maps DMA-BUF memory and calls `ConvertToI420`, then unmaps it. It accepts only
linear/invalid modifiers in that branch and drops other modifiers. This is a
concrete CPU-conversion trap for a proposed bridge, **not evidence that the
normal PipeWire screenshare uses that activity path**.

Relevant local evidence: `native-activity-frame.txt`,
`native-pipewire-process.txt`, `native-source-fields.txt` and
`native-source-candidates.txt` under the research output directory. The header
is at `out/screenshare-lab/probe/runtime/include/electron_video_shared.h`;
the pinned API patch is [Discord's integration patch](../../patches/discord/44/integration.patch).

Internal C++ `SetExternalVideoSource` symbols exist, but exporting a symbol does
not make its ABI a supported plugin contract. The inspected JS desktop-source
options include platform-specific hook choices; those do not themselves enable
a Linux vkcapture consumer. Adding a settings toggle or changing Chromium flags
would not complete this integration.

### Feasible integration routes to investigate

1. **Improve the existing native PipeWire path.** Least change to selection and
   transport. Add counters and narrowly supported corrections where possible.
   Native binary changes need exact-version guards and a safe unsupported-version
   outcome; the existing H.264 patch is not a general capture extension API.
2. **DPE capture producer -> scoped PipeWire DMA-BUF stream -> native consumer.**
   Potentially reuses Discord's current GPU intake and encoder. First prove
   source selection, node routing and access through the correct PipeWire
   connection. The portal's scoped FD cannot be assumed to expose an arbitrary
   new node. A globally published fake camera is not equivalent.
3. **Native GPU-frame adapter with an explicit C ABI.** Define plane metadata,
   timestamps, ownership and completion; adapt into the native video source.
   This is the most direct reusable architecture if a maintainable native seam
   can be established. The current Linux activity conversion would need native
   changes, not just an Electron producer change.

An external recorder's encoded file or elementary stream is not sufficient.
Replacing the encoder also requires target bitrate/framerate feedback,
keyframe requests, codec negotiation, timestamps and integration with Discord's
packetization/encryption/transport. Prefer retaining those responsibilities in
the existing sender until there is a verified encoder adapter.

## Implementation order and acceptance gates

| Order | Concrete deliverable                                                                                                     | Gate before expanding scope                                                                                   |
| ----- | ------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------- |
| 1     | Frame-age and path telemetry around the existing source/encoder; quantify CPU fallback and GPU waits                     | Can distinguish capture, conversion, encode and sender/receiver delay without recording private pixels        |
| 2     | A verified native GPU handoff contract, including scoped source routing if PipeWire is the bridge                        | Imported frames remain on GPU, ownership survives asynchronous encoding, unsupported formats fail predictably |
| 3     | Latest-useful-frame scheduling, demand-limited production and GPU conversion into suitable encoder surfaces where absent | Bounded frame age after overload; no broken sync, stale cursor or added game stutter                          |
| 4     | Direct ext monitor prototype on this niri fork; optional vkcapture game consumer using the same contract                 | Same encoder/settings and source semantics in comparison; useful measured benefit justifies shipping          |
| 5     | KMS monitor helper or Vulkan Video encoder, only when the trace points to those stages                                   | Benefit exceeds added maintenance, compatibility and failure handling                                         |

Orders 1–3 are shared infrastructure for every alternative. The direct Wayland
prototype is a relatively contained local comparison; vkcapture has the more
distinct game-specific opportunity. These are priority/effort judgments, not
claims that direct Wayland is faster than vkcapture.

The common frame contract should contain a stable buffer ID/generation,
monotonic source timestamp plus clock identity, device identity, dimensions,
DRM format/modifier, per-plane object mapping/offset/stride, crop/transform/color
metadata, readiness and release/completion ownership. Reconfiguration retires
old buffers only after their consumers finish. Import validation must handle
multi-plane layouts and distinguish sync-object FDs from image-plane FDs.

Bound pending work rather than assuming a one-buffer allocation is always
optimal: retain enough surfaces for safe overlap, keep at most a small bounded
set of pending useful frames, and skip stale work before conversion. Respect
source cancellation and keep window/monitor semantics on fallback. Do not use
global linear-buffer, compression-disable or maximum-priority switches as a
substitute for negotiation and timing evidence.

Before copying implementation code, check its license and integration boundary:
the reviewed GSR snapshot states GPL-3.0-only; vkcapture's source headers allow
GPL-2.0-or-later, while wl-screenrec uses Apache-2.0. Reusing algorithms and designing an adapter is a
different deliverable from incorporating project code into a distributable
native component. This research does not import any of those implementations
into DPE build inputs.
[Vkcapture source license][vk-protocol], [wl-screenrec license][wl-license].

## Deferred validation: what would prove an improvement

No tests below were run against the active Discord session. Future runtime
checks need fresh profiles and a user-approved time for load, as already
requested. Offline work can cover descriptor validation, queue selection,
clock conversion, reconfiguration and release bookkeeping first.

Use a synthetic moving source with sequence/timing markers, then separately
test actual application/window behavior. Compare backend changes with the same
resolution, FPS, codec, bitrate policy, network and receiver. Test the H.264
correction separately so a capture change does not receive credit for an
encoder fix. Do not save private desktop or webcam pixels.

| Dimension   | Cases and observations                                                                                                                                             |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Cadence     | 1080p30/60, static content, cursor-only updates, rapid motion, high-refresh/VRR producer; source vs sent vs decoded unique frames                                  |
| Pressure    | CPU-only, GPU rendering, video encode and memory pressure separately; then realistic mixed load and recovery                                                       |
| Sources     | Monitor, native Wayland window, XWayland window, supported hooked game; occlusion/minimize, source close and restart                                               |
| Buffers     | Negotiated modifiers, import failure, CPU fallback, resize, rotation/fractional scaling, HDR-to-SDR/color range, multiple planes and multiple GPUs where available |
| Timing      | p50/p95/p99 source-to-dequeue age, fence waits, conversion/encode submission and completion, queue age, pacer delay and viewer presentation                        |
| Quality     | Actual output dimensions/FPS, target vs encoded bitrate, frame-drop/adaptation reason, keyframe response, viewer decode and network loss/RTT                       |
| Host impact | Game frame-time percentiles, compositor missed deadlines, CPU time, GPU work and allocation growth; local preview enabled/disabled separately                      |

Keep clock domains explicit. GPU timestamps and remote receiver clocks cannot
be subtracted as if synchronized. Use stage-local durations, clock mapping or
properly synchronized end-to-end markers. Recording 60 input callbacks per
second is not a substitute for those measurements.

Ship an alternative only if it improves viewer frame age/quality at comparable
settings without unacceptable game frame-time regression. A plateaued GPU can
still delay conversion or video work even when capture itself is cheap; a
backend switch cannot guarantee quality under every saturation condition.

## Source provenance and limits

GitHub source snapshots reviewed: obs-vkcapture
`8a2c43295fae2ede8ffd382a1581281ceb6153c0`; wl-screenrec
`32f7810f3ef7543f4460c65851687435f6a24be1`; OBS
`65c320b46e1af5c7a555161aea6182cdf7a8c524`; the niri fork commit listed above.
`out/capture-research-20261102/source-manifest.json` records fetched files, URLs
and blob hashes. Unpinned documentation links describe their state on the
research date, not a guarantee about future releases.

Verified GSR archive SHA-256:
`699ee6af0530e01d446c25e1e1372e21d97502f5484c9bd6a09a665c91024483`.
Manifest SHA-512:
`7049d8ffe4235141e31f9fa00c35e22b45f7bf3b4968134636a9f9845f9d77f2937a531c5cfe40988305d22f5987a66d1f8ce74d88856bfea2746710a12e029c`.
GSR tree links below identify that reviewed revision; its verified archive was
used when the cgit web pages were bot-blocked. Wayland Explorer links render
the protocol specifications, rather than providing benchmark evidence.

Unresolved by this research: the active share's per-frame fallback rate and
modifier, exact conversion copy count, stage latency under load, cause of the
falling bitrate target, maintenance feasibility of a native adapter, and the
best alternative measured on this machine. These are explicit implementation
and validation gates, not reasons to assume all backends are equally good.

[portal]: https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.ScreenCast.html
[pw-dmabuf]: https://docs.pipewire.org/1.4/page_dma_buf.html
[kernel-dmabuf]: https://docs.kernel.org/driver-api/dma-buf.html
[niri-registration]: https://github.com/urayde/niri/blob/3af9296f088547de86c4ad19f9b293d94dea49b5/src/niri.rs#L2627
[niri-image-copy]: https://github.com/urayde/niri/blob/3af9296f088547de86c4ad19f9b293d94dea49b5/src/handlers/image_copy_capture.rs
[niri-pw]: https://github.com/urayde/niri/blob/3af9296f088547de86c4ad19f9b293d94dea49b5/src/screencasting/pw_utils.rs
[obs-pw]: https://github.com/obsproject/obs-studio/blob/65c320b46e1af5c7a555161aea6182cdf7a8c524/plugins/linux-pipewire/pipewire.c
[obs-vaapi]: https://github.com/obsproject/obs-studio/blob/65c320b46e1af5c7a555161aea6182cdf7a8c524/plugins/obs-ffmpeg/obs-ffmpeg-vaapi.c
[obs-xcomp]: https://github.com/obsproject/obs-studio/blob/65c320b46e1af5c7a555161aea6182cdf7a8c524/plugins/linux-capture/xcomposite-input.c
[obs-xshm]: https://github.com/obsproject/obs-studio/blob/65c320b46e1af5c7a555161aea6182cdf7a8c524/plugins/linux-capture/xshm-input.c
[gsr-archive]: https://dec05eba.com/snapshot/gpu-screen-recorder.git.r1536.099a55b.tar.gz
[gsr-manifest]: https://github.com/flathub/com.dec05eba.gpu_screen_recorder/blob/master/com.dec05eba.gpu_screen_recorder.yml
[gsr-vaapi]: https://git.dec05eba.com/gpu-screen-recorder/tree/src/encoder/video/vaapi.c?id=099a55b
[gsr-conversion]: https://git.dec05eba.com/gpu-screen-recorder/tree/src/color_conversion.c?id=099a55b
[gsr-kms-helper]: https://git.dec05eba.com/gpu-screen-recorder/tree/kms/server/kms_server.c?id=099a55b
[gsr-kms]: https://git.dec05eba.com/gpu-screen-recorder/tree/src/capture/kms.c?id=099a55b
[gsr-pw]: https://git.dec05eba.com/gpu-screen-recorder/tree/src/pipewire_video.c?id=099a55b
[gsr-codec]: https://git.dec05eba.com/gpu-screen-recorder/tree/src/recorder/video_codec.c?id=099a55b
[gsr-recorder]: https://git.dec05eba.com/gpu-screen-recorder/tree/src/recorder/recorder.c?id=099a55b
[gsr-vulkan]: https://git.dec05eba.com/gpu-screen-recorder/tree/src/encoder/video/vulkan.c?id=099a55b
[kmsgrab]: https://ffmpeg.org/ffmpeg-devices.html#kmsgrab
[vk-readme]: https://github.com/nowrep/obs-vkcapture/blob/8a2c43295fae2ede8ffd382a1581281ceb6153c0/README.md
[vk-layer]: https://github.com/nowrep/obs-vkcapture/blob/8a2c43295fae2ede8ffd382a1581281ceb6153c0/src/vklayer.c
[vk-protocol]: https://github.com/nowrep/obs-vkcapture/blob/8a2c43295fae2ede8ffd382a1581281ceb6153c0/src/capture.h
[vk-consumer]: https://github.com/nowrep/obs-vkcapture/blob/8a2c43295fae2ede8ffd382a1581281ceb6153c0/src/vkcapture.c
[wl-main]: https://github.com/rosalyntg/wl-screenrec/blob/32f7810f3ef7543f4460c65851687435f6a24be1/src/main.rs
[wl-filter]: https://github.com/rosalyntg/wl-screenrec/blob/32f7810f3ef7543f4460c65851687435f6a24be1/src/filter.rs
[wl-fps]: https://github.com/rosalyntg/wl-screenrec/blob/32f7810f3ef7543f4460c65851687435f6a24be1/src/fps_limit.rs
[wl-license]: https://github.com/rosalyntg/wl-screenrec/blob/32f7810f3ef7543f4460c65851687435f6a24be1/LICENSE
[ext-copy]: https://wayland.app/protocols/ext-image-copy-capture-v1
[wlr-copy]: https://wayland.app/protocols/wlr-screencopy-unstable-v1
[wlr-export]: https://wayland.app/protocols/wlr-export-dmabuf-unstable-v1
[gamescope]: https://github.com/ValveSoftware/gamescope/blob/master/src/pipewire.cpp
[nvfbc]: https://developer.nvidia.com/capture-sdk
[v4l2loopback]: https://github.com/v4l2loopback/v4l2loopback/blob/main/v4l2loopback.c
[vulkan-encode]: https://github.com/KhronosGroup/Vulkan-Docs/blob/main/chapters/video/encode.adoc
[sunshine]: https://docs.lizardbyte.dev/projects/sunshine/master/md_docs_2troubleshooting.html
