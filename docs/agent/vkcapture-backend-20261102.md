# Optional game capture backend — 2026-11-02

The selectable backend is a DPE-owned PipeWire producer plus opt-in OpenGL/Vulkan
hooks based on [obs-vkcapture commit 8a2c43295fae2ede8ffd382a1581281ceb6153c0](https://github.com/nowrep/obs-vkcapture/tree/8a2c43295fae2ede8ffd382a1581281ceb6153c0).
It does not attach to an already running game, register a global Vulkan layer,
or replace the desktop portal. Launching a game through the bundled
`capture/vkcapture/gamecapture-launch.py -- command` loads only that command's
hooks. Existing preload/layer entries are preserved. The broker lists opted-in
process labels and opaque selection tokens; connecting alone does not capture.

## Handoff and ownership

`vkcapture-wire.h` defines a separate, versioned, fixed-size protocol over an
owned mode-0600 filesystem `SOCK_SEQPACKET` socket. Both endpoints check same-UID
peer credentials. A broker-owned mode-0700 runtime directory prevents another
user from listing/selecting peers. No OBS socket is reused. The selected producer
receives that peer socket by inherited descriptor; its output is a narrowly
scoped PipeWire node consumed through the native picker bridge.

The graphics hook owns one export texture. Capture admission runs before the
copy and is bounded to the current requested FPS. Where supported, the hook
exports an EGL native fence or a dedicated Vulkan SYNC_FD semaphore with the
frame. The consumer waits asynchronously for that actual completion fence before
sampling; the hook cannot overwrite the texture until the consumer acknowledges
the exact generation/sequence. The consumer imports the actual format, plane
layout and modifier, performs a GPU copy into an owned linear BGRA pool, and
returns the lease only after its own EGL native fence succeeds. Both fence paths
validate successful status, since a readable sync_file can also contain an error.
Contexts without fence export retain zero-timeout polling on later game presents;
that safe fallback has additional latency and may halve throughput at matching
source/share FPS. There is no CPU pixel mapping/readback, unbounded frame
queue, or waiting for the consumer in the application's present path.

The Vulkan patch retains the upstream intrusive-list member at offset zero and
initializes its additional fence to `VK_NULL_HANDLE`; initial smoke checks caught
both integration mistakes before staging the working bundle. Swapchain changes
use increasing generations and update the same PipeWire stream. A crossed
acknowledgment for the exact retired lease cannot release a new lease. Incoming
textures are deferred until an outstanding consumer GPU copy completes.

The exporter supplies its GL/Vulkan device UUID. `--render-node auto` checks at
most 16 render nodes and accepts the matching device only; it never assumes a
vendor or the first enumerated GPU. Actual EGL import and DMA-BUF allocation
must succeed. This proof is on AMD; NVIDIA/Intel operation is capability-checked
but has not been tested on physical hardware here.

## Control and supported scope

The broker uses JSON lines on stdin/stdout:

- `{id,op:'list'}` returns `{token,name,available}` entries.
- `{id,op:'start',token,fps}` selects one peer; no second active source is allowed.
- `{id,op:'rate',fps}` requests 1–240 FPS through producer stdin `rate N\n`.
  Format publication waits for an outstanding GPU copy to finish; the node ID
  remains stable. The RPC acknowledges the request, not measured delivery FPS.
- `{id,op:'stop'}` terminates only the owned producer; `{id,op:'quit'}` closes the
  broker. EOF and termination clean up owned sockets and children.

Ready events contain `nodeId`, `width`, `height`, `generation` and requested
`fps`. Failed ended events include a bounded `errorCode`: `unsupported-buffer`
for rejected import format/device/modifier, otherwise `capture-failed`. Native
picker setup must reject these immediately instead of waiting for readiness.

Supported input is single-device SDR RGB8 DMA-BUF capture with the required
GL/EGL extensions and normal GPU import support. FP16/HDR, unsupported modifiers,
missing exporter UUID, and cross-GPU paths fail explicitly. This implementation
does not add CPU fallback to make an unavailable method appear supported. Bundle
paths containing loader separators (spaces/colon) are rejected rather than
constructing an incorrect `LD_PRELOAD`/layer path. Only 64-bit Linux hooks ship.

## Packaging and evidence

`stage_capture_backends.py` builds the small helpers without starting a display
or GPU, validates every shipped file, and stages both this backend and
`dpe-wayland-source`. Local builds use it directly; generated recipes carry a
prebuilt checksummed bundle and therefore require no native build at install
staging. The pinned, patched GPL-2.0-or-later hook source, its LICENSE, MIT helper
source and notices ship alongside the binaries. Builder CI dependencies include
Wayland, PipeWire, GBM/EGL/GLES, Vulkan/OpenGL headers, CMake and Ninja.

Focused tests cover descriptor ownership, readiness before GPU completion,
one-frame leases, wrong/stale acknowledgments, crossed retirement acknowledgments,
source selection, rate commands, producer EOF cleanup, existing-socket rejection,
launch environment preservation, bundle tampering and standalone recipe assets.
The C handoff runs under ASan/UBSan without opening a graphics device.

Two bounded source-level proofs used dedicated synthetic applications and a
private PipeWire daemon. The receiver inspected DMA-BUF descriptors, dimensions
and advancing sequence metadata; it did not map or save pixels:

- OpenGL: 120 delivered 320×240 frames at requested 30 FPS; final sequence 135.
  Evidence: `/mnt/storage/dpe-capture-backends-20261102/vkcapture-smoke/report.json`.
- Vulkan RGB8: 120 delivered 320×240 frames at requested 30 FPS; final sequence 136.
  Evidence: `/mnt/storage/dpe-capture-backends-20261102/vkcapture-vulkan-rgb8/report.json`.

The standard Wayland `vkcube` chose FP16 scRGB and correctly failed import. An
owned SDL Vulkan clear-color source selecting RGB8 explicitly established the
supported path. These checks establish delivery and ownership for these sources;
they do not establish remote viewer performance, HDR conversion, or a live
Discord selection flow. The later lifecycle checks below cover bounded resize
and pause cases. The root integration checks cover
the native picker separately. No existing game or Discord session was injected,
restarted or reconfigured.

## Lifecycle correction and matched-FPS checks

A subsequent user report of captures stopping exposed several producer defects:

- PipeWire can remove a `pw_buffer` while its GPU copy is outstanding. The producer
  now detaches that expired PipeWire object and keeps only its owned GPU allocation
  until completion. Normal renegotiation no longer ends the capture.
- Successive game textures and frames can overtake an old pending copy. The newest
  descriptor replaces a superseded pending generation; obsolete frames are dropped
  safely without accepting an invalid lease or interpreting them as protocol errors.
- Output allocation follows the accepted Format dimensions, independently of the
  newest game texture. Stale pool buffers are returned without delivery, and a
  previously empty chunk regains its full size on successful reuse.
- FPS updates advertise variable frame rate with a bounded maximum. The previous
  valid format remains usable until replaced; a requested rate increase stays
  clamped to the old negotiated maximum until acknowledgment. Format NULL suspends
  submission without destroying the selected source.
- SIGTERM/SIGINT are blocked before EGL/PipeWire create worker threads, allowing
  the main-loop signal handler to cleanly stop and report counters.

The callback regression executes the production C producer with deterministic
pending GPU work, actual Unix sockets and descriptor ownership under ASan/UBSan.
It covers pool retirement, multiple generation changes, accepted allocation
sizes, empty-buffer reuse, async FPS acknowledgment, delayed input fences,
cancellation and negative fence status.

A 12-second GL run kept one PipeWire node across three sizes, rapid 60→30→60,
then 15→60 FPS, and a 1.2-second render pause. It delivered 516 frames, with the
final four seconds at 60 FPS. Its first cleanup assertion correctly caught
termination by SIGTERM (-15); the signal-mask correction then produced clean
code-0/failed-false endings in the following matched-FPS checks. The failed
cleanup report remains at
`/mnt/storage/dpe-capture-lifecycle-20261102/gl-rate-resize/report.json`.

The old polling handoff needed another game present to notice completion and a
later present to observe release. A controlled 60 FPS EGL source therefore
produced exactly 30 FPS after startup. Exporting the completion fence removed
that extra-present dependency: the same source delivered 60 FPS in each of four
steady one-second intervals, with 306 exported-fence frames and a clean stop.
Evidence: `gl60-poll-b/report.json` and `gl60-fenced-b/report.json` under
`/mnt/storage/dpe-capture-lifecycle-20261102/`.

The five-second Vulkan RGB8 check also exercised actual exported fences (276),
kept its node and exited cleanly. Its steady delivery was 59, 53, 50 and 48 FPS
from a measured 60 FPS source, so this result does **not** establish perfect
60 FPS throughput or a latency advantage for Vulkan. Evidence:
`/mnt/storage/dpe-capture-lifecycle-20261102/vulkan60-fenced/report.json`.
Independent review additionally corrected oversized Vulkan wait lists, restoring
original present dependencies after a failed QueueSubmit, and detecting readable
but failed helper-output fences.

Two later 20-second native tests exercised the revised game backend through
the production capture picker and transport adapters, AMD H.264 hardware encode,
and Electron receiving at Source 1920×1080/60. The owned EGL workload ran at
60 and 240 FPS respectively. Both passed all 14 checks, retained full dimensions,
decoded every sampled marker and logged zero GPU-rendition CPU fallbacks:

| Source FPS | Unique received frames | Median marker-to-receiver latency | Exported-fence frames |
| ---------- | ---------------------- | --------------------------------- | --------------------- |
| 60         | 1,188                  | 58 ms                             | 2,177                 |
| 240        | 1,189                  | 56 ms                             | 2,188                 |

Both producers reported `failed:false` and exited with code 0. Reports are
`/mnt/storage/dpe-capture-pipeline-20261102/game-60-source/native/report.json`
and `game-240-source/native/report.json` under the same parent; each run's
`producer-events.json` retains fence and cleanup counters. This verifies the
local native encode/decode pipeline for those bounded workloads. It does not
include an SFU, internet congestion, long-duration gaming, or the Vulkan path.

Fence semantics are grounded in
[EGL_ANDROID_native_fence_sync](https://registry.khronos.org/EGL/extensions/ANDROID/EGL_ANDROID_native_fence_sync.txt),
[Vulkan semaphore FD export](https://docs.vulkan.org/refpages/latest/refpages/source/vkGetSemaphoreFdKHR.html),
[Linux sync_file polling/status](https://github.com/torvalds/linux/blob/master/drivers/dma-buf/sync_file.c),
and [PipeWire buffer lifetime](https://docs.pipewire.org/page_streams.html).
Existing running games retain their loaded hooks; the new exported-fence path
applies when the game is next launched through the updated wrapper.

## Cost comparison: what this backend can and cannot improve

The pinned OBS Vulkan hook copies/blits the swapchain image into an exportable
image and chains its semaphore into presentation. The OBS plugin imports that
image and renders it into the scene; these operations are visible in
[vklayer.c](https://github.com/nowrep/obs-vkcapture/blob/8a2c43295fae2ede8ffd382a1581281ceb6153c0/src/vklayer.c)
and [vkcapture.c](https://github.com/nowrep/obs-vkcapture/blob/8a2c43295fae2ede8ffd382a1581281ceb6153c0/src/vkcapture.c).
DPE additionally copies the imported game image into an owned linear BGRA pool
before the native capturer's scale/color-conversion/encode stages. It avoids
CPU pixel readback and can avoid compositing unrelated windows, but it is not a
zero-copy route and cannot be assumed faster than a working DMA-BUF portal.

The retained primary GPU Screen Recorder snapshot
[r1536.099a55b](https://dec05eba.com/snapshot/gpu-screen-recorder.git.r1536.099a55b.tar.gz)
(SHA-256 `699ee6af0530e01d446c25e1e1372e21d97502f5484c9bd6a09a665c91024483`)
exports the encoder's VA surface into EGL textures and draws color conversion
into its Y/UV planes. That is a materially different pipeline from inserting an
extra RGB producer ahead of Discord's existing encoder. Its design supports
investigating a future direct encoder-surface handoff; it does not justify
claiming the current DPE game backend inherits GSR's resource use or latency.
The full-pipeline comparison must measure game FPS, capture-to-viewer latency,
delivered frames, encode/decode path and utilization with matched workloads.
