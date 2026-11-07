# PipeWire-free game and KMS capture — 5 November 2026

Bundled game capture and KMS can now pass DMA-BUF descriptors directly into
Discord's native capture callback. The producer creates no PipeWire node, and
the consumer creates no PipeWire capture stream. Discord retains its existing
codec negotiation, congestion control, packets and receiver behavior.

This is a new implementation of the direct GPU capture architecture, not a
copy of GPU Screen Recorder or a claim of equivalent performance on every GPU.
Evidence is retained in `out/direct-capture-20261105/`.

## Frame path and ownership

`dpe-direct-source` receives the selected bundled game hook or the authorized
KMS helper. A private, same-user `SOCK_SEQPACKET` endpoint carries bounded frame
metadata, DMA-BUF descriptors and optional acquire sync-file descriptors using
`SCM_RIGHTS`. No frame pixels cross a CPU socket or shared-memory transport.
The existing picker transfers ownership of that connection to the native
adapter using a reserved configuration identifier, not a PipeWire node.

The native adapter imports RGB buffers through VAAPI VPP. One GPU operation
converts/scales the image to NV12 and composites the cursor. It waits for the
acquire fence before reading and for actual VPP completion before releasing
the producer's source lease. Four NV12 outputs and eight cached input imports
bound retained allocations. Held encoder/CPU leases keep their output alive
after source shutdown; an occupied output is never reused.

Owned frames use reviewed C exports at the Discord boundary. The adapter does
not construct private Rust frame layouts. Calls for portal, camera and other
foreign frames forward to the original exports. Whole-module hashes, export
sizes, relocations, accessor bytes and mapping permissions guard installation.
The new direct scope currently covers prepared native SHA-256
`7891b5ef54457b80df036a8b38d59045e9dbcca881d8c8b66f29e88fd050f479`.
Other native versions retain their existing capture route until reviewed.

The private VAAPI backend accepts owned NV12 inputs for H.264, HEVC and AV1.
It holds each surface lease until FFmpeg releases it. Discord's synchronous
frame-activity detector previously caused one full-frame CPU readback each
second; CPU conversion is now declined during capture callback delivery.
Software encoders can request I420 on their separate queue. Hardware checks
require zero CPU readbacks, actual GPU encoder inputs and decoded frames.

X11 and supported Wayland game cursors retain the existing source identity
checks. A sealed, bounded cursor bitmap can be uploaded when cursor metadata
provides pixels; this is at most 256×256, not a screen readback. KMS cursors stay
in DMA-BUFs. The Wayland cursor adapter uses a standalone `pw_loop` utility for
its event sources; it opens no PipeWire core, stream or node. PipeWire remains
available for audio, portal capture, direct Wayland capture and explicitly
selected stock OBS hook compatibility.

## Validation

- Native descriptor tests exercise truncation, descriptor overflow, malformed
  headers, close-on-exec, EOF and repeated rejection without descriptor leaks.
- Native worker tests exercise pause, 60→30→60 changes, invalid frames, source
  loss, ownership and repeated destruction under address/undefined sanitizers.
- The physical GPU fixture checks RGB→NV12→I420 colors, clipped cursor blending,
  bounded output exhaustion and leases surviving capture teardown.
- The native sender/receiver validates picker cancellation and reuse, actual
  hardware encoding, receiver decoding, geometry, marker integrity, direct
  transport counters, zero PipeWire capture frames and zero CPU readbacks.
- Main-process and renderer tests cover endpoint handoff, helper cleanup and
  attribution of direct counters to one active share.

Initial development failures are retained: the producer originally exited when
an unconsumed picker connection closed, and the first working hardware stream
still triggered ten synchronous activity-analysis readbacks. These runs are
excluded from performance comparisons.

## Limits

Physical validation uses the RX 7900 GRE, Mesa 26.2.5, Niri and the retained
Electron/native module on this machine. The direct import/conversion backend
requires VAAPI VPP and compatible DMA-BUF formats/modifiers. NVIDIA direct
encoding, Intel hardware, HDR/tone mapping, stock OBS hooks, live account calls
and real games are not qualified by these synthetic checks. Unsupported source
formats or synchronization fail capture instead of selecting another source.

The resource harness includes a local receiver and its pixel readback. Its CPU
and memory totals must not be described as sender-only screensharing usage.
The process-tree totals also omit existing host services outside the monitored
groups, including the host PipeWire daemon and Xwayland server. See the
[current comparison audit](current-pipewire-comparison-20261105.md).
Matched comparisons must preserve source rate, geometry, encoder, bitrate,
delivery cadence and pressure, and must report actual unique decoded FPS.

## Matched results

Ten valid cases: two 16-second repeats per path, with order reversed for the
second repeat. Same RX 7900 GRE/Niri setup, owned 1920×1080 source capped at
600 FPS, requested 1080p60 H.264 VAAPI at 6 Mbps, local native sender/receiver
and normal delivery cadence. FPS counts unique decoded markers in seconds
3–14; CPU/RSS use the runner's steady resource window. Values below are medians
of the two case summaries; latency uses each run's full-phase percentile.

| Method                | CPU, one CPU % | Unique FPS | CPU ms / unique frame | Latency p50 / p95, ms | Summed RSS, MiB | GPU busy % | Media Mbps |
| --------------------- | -------------- | ---------- | --------------------- | --------------------- | --------------- | ---------- | ---------- |
| Portal / PipeWire     | 73.3           | 59.9       | 12.24                 | 39.5 / 43.0           | 1392            | 19.9       | 6.21       |
| Game / PipeWire relay | 74.5           | 60.0       | 12.42                 | 40.5 / 42.5           | 1588            | 19.6       | 6.01       |
| Game / direct GPU     | 72.7           | 57.9       | 12.57                 | 19.5 / 21.5           | 1531            | 18.5       | 6.31       |
| KMS / PipeWire        | 75.9           | 59.9       | 12.68                 | 63.5 / 67.0           | 1565            | 20.1       | 6.04       |
| KMS / direct GPU      | 72.7           | 58.4       | 12.45                 | 23.0 / 27.0           | 1482            | 18.4       | 6.39       |

Direct game latency falls by about 52% versus the old relay; KMS latency falls
by about 64%. Total CPU falls by about 2.4% and 4.3%, respectively, while summed
RSS falls by about 57 and 82 MiB. These RSS totals count shared pages repeatedly.
GPU busy is device-wide utilization, not capture-only engine accounting. No new
board-power or whole-system energy measurement was made in this direct pass.

The CPU reduction does **not** establish a large per-frame efficiency gain:
direct H.264 delivers about 58–59 unique FPS, and game CPU milliseconds per
unique frame are slightly higher. Native logs attribute discarded frames to
WebRTC's media frame dropper; the producer receives/converts approximately
60 FPS without exhausting its output pool. Congestion control and frame
dropping remain enabled. The private H.264 encoder also produces a different
actual bitrate from the old native encoder. Objective equal-quality efficiency
was not measured. Removing PipeWire is an architectural change, not evidence
that PipeWire previously consumed most of the resources.

The matched block precedes the final H.264 VUI color metadata and incompatible
hardware-encoder rejection guards. `kms-final` separately passes the final
implementation: 1,204 converted frames, 1,164 GPU encoder inputs, zero CPU
readbacks, zero capture failures, no PipeWire stream and clean teardown over
20 seconds. These final changes do not bypass bandwidth adaptation.

`game-lifecycle` passes all 16 lifecycle checks at a 60 FPS source. It observes
944×1018 after leaving fullscreen, returns to 1920×1080, delivers about 30 FPS
after the rate reduction and about 60 FPS after rate restoration and source
recovery. The first short phase contains only about 46 FPS; later recovery
does not erase that initial result. Source-process pause produces no unique
frames in the measured paused interval. Same-size graphics-context replacement
and real-game context recreation remain unqualified.

`game-av1-720-fixed` passes a separate 20-second AV1 case at **1280×720**:
1,201 converted frames, 1,200 GPU encoder inputs, zero CPU readbacks and 1,195
unique decoded markers, with 17/19 ms p50/p95 latency. This does not establish
1080p AV1 performance. The earlier AV1 invocation supplied an H.264-only decoder
argument and failed before native capture; that setup failure is retained.

One 20-second GPU-pressure pair passes both methods' delivery gates. The
constrained phase contains 400 direct-game unique frames versus 376 portal
frames, with 24/27 ms versus 46/60 ms p50/p95 latency. This single pair supports
a latency/freshness benefit under the exercised load; it is not a repeated
pressure benchmark or a real-game FPS-loss result.

Artifacts: `benchmark-summary.json`, `qualification.json`, `matched-r*-*`,
`game-lifecycle`, `kms-final`, `game-av1-720-fixed`, `game-pressure` and
`portal-pressure` under the retained output directory. Keep the source/decoder
and full-pipeline limits above when presenting these numbers.

`kms-software-fixed` passes a separate VP8/libvpx sender/receiver check. It
performs 721 CPU readbacks for 724 converted frames, as expected for software
encoding; this is outside the zero-readback hardware performance claim. The
first software invocation delivered frames but requested the Electron decoder
gate while libvpx was actually used, so its strict decoder check failed and the
correct software-decoder case was rerun. `h264-vui.json` independently confirms
12 decoded synthetic H.264 frames with limited-range BT.709 VUI metadata.

## Final checks

`bundle-final` verifies its complete 169-file inventory and checksums.
`final-runtime` passes the final compiled helper and coherent game bundle with
an owned source submitting 60.008 FPS. It converts/encodes 668 frames over the
12-second native interval, with zero CPU readbacks. This delivery-only check
overlapped repository tests and is excluded from matched performance claims;
it does not demonstrate 60 unique FPS. Repository lint passes; the full suite passes 747 Python
tests and 447 JavaScript tests, with three JavaScript skips. Native ownership,
lease, picker and encoder tests include sanitizer checks. No Electron runtime
was rebuilt, no installed app was restarted and no account call was made.

The manual GPU fixture uses generated colors and checks only the selected
render device:

```sh
cargo build --locked --release -p dpe-native
cc -O2 -Wall -Wextra -Werror tests/screenshare/direct-gpu-check.c \
  out/rust-target/release/libdpe_native.a -pthread -ldl -lm -Wl,--gc-sections \
  $(pkg-config --cflags --libs gbm egl glesv2 libva-drm libdrm) \
  -o out/direct-gpu-check
out/direct-gpu-check
```

The fixture explicitly opens `/dev/dri/renderD128`; choose the intended test GPU
in its source before running it on another machine. It opens no desktop source.
