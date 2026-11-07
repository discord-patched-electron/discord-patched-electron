# Current direct capture versus PipeWire — 5–6 November 2026

Direct capture clearly reduces local latency. The current fast-source block
does not establish lower CPU per delivered frame; the matching-rate game block
shows a useful-frame/CPU benefit where portal produces repeated pictures.
Neither block measures sender-only overhead or proves GSR-level efficiency.

This comparison follows the [direct GPU path](direct-gpu-capture-20261105.md),
[encoder import reuse](encoder-import-reuse-20261105.md) and
[matching-rate pacing fix](game-capture-pacing-20261105.md). It uses the final
coherent capture bundle and frozen native helper from that pacing qualification,
rather than combining older PipeWire measurements with newer direct results.

Evidence lives in `out/capture-vs-pipewire-20261105/`. The retained runner
uses fresh native profiles, the same runtime/module/OpenH264/source identities,
normal delivery cadence and the same native local sender/receiver. Every case
requests 1920×1080/60 H.264 VAAPI at 6 Mbps and lasts 20 seconds. The owned source
uses GLX on DP-2 under Niri. Portal uses Niri's ScreenCast backend, which supplies
its portal capture route, excluding picker interaction. Game uses the bundled
relay hook with its GL semaphore bridge; KMS uses existing helper authorization.

There are three repeats of portal, direct game and direct KMS at a 600 FPS source
cap, with rotating method order. Two reversed-order portal/game pairs use a
60 FPS source. FPS counts unique decoded markers in seconds 3–18; resource
sampling uses the harness's steady window. Summary values are medians of case
summaries, not pooled measurements or confidence intervals.

## Current results: fast source

Three repeats per method, source capped at 600 FPS, requested 1080p60:

| Method            | Sampled CPU, one core % | Unique FPS | CPU ms / unique frame | Latency p50 / p95, ms |
| ----------------- | ----------------------- | ---------- | --------------------- | --------------------- |
| Portal / PipeWire | 74.34                   | 60.00      | 12.39                 | 48 / 51               |
| Game / direct GPU | 71.96                   | 57.53      | 12.51                 | 20 / 22               |
| KMS / direct GPU  | 74.11                   | 58.47      | 12.50                 | 24 / 27               |

Direct game uses about 3.2% less sampled CPU but delivers about 4.1% fewer unique
frames. Direct KMS uses about 0.3% less sampled CPU and delivers about 2.6% fewer
frames. CPU per delivered frame is about 1% higher for both direct methods.
These runs do not establish an efficiency improvement at equal useful
throughput. The difference between median whole-loop ratios is also too small
to claim a reliable regression from three short runs with variable desktop work.

The clear benefit is latency: game is about 58% lower and KMS 50% lower than
portal in this block. Median per-case p95 falls from 51 ms to 22/27 ms. These
latencies end at local decoded-pixel readback, excluding WAN/SFU and physical
monitor scanout.

| Other median metric          | Portal / PipeWire | Game / direct GPU | KMS / direct GPU |
| ---------------------------- | ----------------- | ----------------- | ---------------- |
| Native CPU, one core %       | 46.84             | 44.57             | 45.91            |
| Capture session/helper CPU % | 0.00              | 0.25              | 1.51             |
| Summed mean RSS, MiB         | 1376.5            | 1516.1            | 1461.2           |
| Whole-device GPU busy %      | 19.38             | 18.31             | 18.63            |
| Native DRM encoder engine %  | 14.30             | 13.98             | 14.18            |
| Observed media bitrate, Mbps | 5.80              | 6.21              | 6.25             |

The portal session-helper CPU rounds to zero; it does not include the compositor
or host PipeWire daemon. Those rows must not be read as PipeWire itself having
zero cost. Direct game has roughly 140 MiB higher summed RSS and KMS 85 MiB
higher; this does not establish physical RAM growth of the same size. No new
board-power, CPU-package-power or whole-PC energy comparison was made.

## Current results: source capped at 60 FPS

Two reversed-order portal/game pairs, otherwise the same setup:

| Method            | Sampled CPU, one core % | Unique FPS | CPU ms / unique frame | Latency p50 / p95, ms |
| ----------------- | ----------------------- | ---------- | --------------------- | --------------------- |
| Portal / PipeWire | 61.98                   | 51.70      | 12.10                 | 56.0 / 58.5           |
| Game / direct GPU | 57.11                   | 57.50      | 9.93                  | 20.5 / 23.0           |

Here direct game capture uses about 7.8% less sampled CPU and delivers more
distinct pictures. The portal results vary widely: 56.73 and 46.67 unique FPS,
versus 57.47 and 57.53 for direct game. Portal encodes 1,197 frames in each
20-second session but presents 74 and 273 repeated decoded picture markers;
neither session reports sender rate-limiter drops. Direct game reports zero
repeated markers but 45/46 rate-limiter drops. All four sources actually submit
60.00–60.02 FPS at 1920×1080. Lower source throughput therefore does not explain
the portal repeats.

These two pairs support a freshness/efficiency benefit for direct game in this
matching-rate fixture. They do not establish the repeat's exact compositor,
capture or scheduling cause, or a universal 60 FPS PipeWire problem. The median
CPU/FPS ratio is about 18% lower for direct game here, with that percentage
strongly affected by the portal repeats. Keep this separate from the fast-source
block, which does not establish a per-frame CPU saving. Current KMS at the
60 FPS source cap was not included in this block.

## What the comparison measures

CPU includes the sender, local receiver/readback, source, helpers and compositor,
with one logical CPU counted as 100%. It is not sender-only Discord overhead.
The ordinary process-tree sampler omits existing host services outside those
groups, including the host PipeWire daemon and Xwayland server. Its totals are
sampled pipeline CPU, not complete session/PC CPU. Supplemental daemon controls
were attempted separately, but did not qualify, as detailed below; no daemon
estimate has been silently added to the primary medians. Host daemon activity
can include other clients and is not automatically capture-only overhead.
Source and compositor costs matter because hook work runs inside the source.
The GPU busy sampler covers the entire device and desktop. DRM engine counters
identify client activity; they are not whole-board power measurements.
Summed RSS double-counts shared mappings and is not proportional/private memory.

The direct path uses the private hardware encoder backend, whereas regular
PipeWire H.264 uses the native encoder. Requested codec, dimensions, frame rate
and bitrate match, but actual rate control and bitrate can differ. This is a
comparison of complete available paths, not an isolated measurement of the cost
of PipeWire transport. Equal objective image quality is not established.

## Remaining work in the implementation

- **Downstream frame loss:** recent direct H.264 runs submit approximately
  60 GPU frames per second but deliver about 58 distinct decoded frames. Native
  logs identify rate-limiter drops. The matching-rate producer bug is fixed;
  bandwidth adaptation and frame dropping remain enabled. A further fix needs
  to preserve those contracts rather than disable them to obtain a higher FPS.
- **GPU work remains:** the GL hook blits the backbuffer into exported RGB
  storage, including orientation in that blit. Direct game capture then uses
  VAAPI VPP for RGB→NV12 and cursor composition. Direct KMS imports the primary
  scanout/cursor descriptors into VPP; the old PipeWire producer's intermediate
  screen-copy buffer is absent on this route. Neither route is a CPU-readback
  capture path with hardware encoding, but neither is free GPU work.
- **Synchronization and driver calls:** conversion still creates/destroys VPP
  parameter buffers and waits for completion before source release and
  cross-display encoder import. Reusing parameters or changing that scheduling
  is a possible experiment, not a demonstrated saving. It must preserve source
  ownership, output-pool bounds and actual completion fences.
- **Static content:** no damage-aware skip has been qualified. Reusing a DMA-BUF
  allocation does not prove that its pixels are unchanged. A safe optimization
  needs trustworthy damage/content information, including cursor changes,
  without introducing full-frame CPU readbacks.

## Missing evidence

The largest gap is sender-only measurement, with the native sender and receiver
in separate processes and memory measured proportionally. Existing thread
counters distinguish some receiver work but cannot split shared GPU-process and
native work. Kernel permissions denied call-stack profiling; they were not changed.

No matched GSR process benchmark has been performed. DPE now has GSR-style
capture ingress on these eligible methods, but Discord's WebRTC sender,
negotiation, congestion control, packetization and receiver remain. A file
recorder's total CPU cannot be compared directly to this instrumented local
streaming round trip.

Other gaps include uncapped real-game FPS loss, Vulkan/native-Wayland games,
older or lower-power CPUs, Intel/NVIDIA drivers, 1440p/4K, HDR, long sessions,
memory-bandwidth/VRAM contention, current matched AV1/HEVC performance, WAN
viewers, and equal-quality encoder comparisons. Earlier isolated AV1/HEVC and
lifecycle checks establish specific delivery/ownership behavior, not this
broader performance coverage. A small saving on this machine does not determine
its size on another machine.

PipeWire is bypassed for video on the qualified bundled game/KMS hardware path.
Application audio, portal capture, compositor-protocol capture and stock OBS
compatibility retain their existing routes. The reviewed native-module hash
and driver/format constraints still apply.

## Primary qualification

All 13 primary cases pass native hardware encoding, Electron decoding,
dimensions, picture-marker integrity, setup/delivery and cleanup. Their sources
submit 599.95–600.02 FPS or 60.00–60.02 FPS, as configured, with zero source fence
timeouts. The eight direct cases report zero video CPU readbacks, no PipeWire
video streaming, zero capture failures and zero live encoder imports after
teardown. Import creations plus hits reconcile with actual GPU encoder inputs.
The five portal cases verify actual PipeWire DMA-BUF capture.

The capture bundle verifies its complete 170-file inventory. Every case uses
the same frozen native helper hash
`a600d7a785e92fdc9064894f6c41d779e04952655f27ac2c30c5256d6c016677` and matching
runtime/module/OpenH264/source identities. `qualification.json` records these
gates; `summary.json` and `medians.json` retain derived metrics. Setup and
supplemental failures remain separate and excluded, as described below.

This pass changes investigation documents and the historical CPU-accounting
descriptions only. Repository lint passes and changed documentation links
resolve. No Electron rebuild, installed application update, account call,
permission change or saved source images/video was involved.

## Runner setup failure

The first portal attempt stopped before native capture: the earlier direct-only
frozen-helper wrapper asserted that the checker requested HEVC support. Portal
H.264 does not request that option. Its workload and portal session shut down
cleanly. The failed output/log are retained under
`setup-failure-portal-helper-assert` and excluded from performance results.

The local wrapper now supplies the same already-verified helper to every method,
including portal, and records the requested build options alongside its actual
frozen hash. The extra private codec support is present in every case; regular
H.264 still selects the native encoder because its frames are not direct-owned
frames. No installed binary or repository implementation changed for this run.

A first external-daemon sampler used the Unix socket's peer credentials. On
this socket-activated setup those identify the socket creator, `systemd --user`,
rather than the PipeWire daemon. Those zero-tick observations are invalid for
PipeWire and retained as `socket-creator-samples.json`/`socket-creator-summary.json`.
The replacement resolves `pipewire.service`'s main PID, verifies its executable
name and UID, and samples separate bounded controls in `daemon-controls/`.
Its first portal control fails native marker/dimension/timing qualification:
945 invalid markers and a source averaging 323.8 FPS instead of the intended 600. Encoding and decoding occurred, but the cause of the invalid content is
not established. No pictures were saved. The portal and owned workload both
shut down cleanly. The remaining control was not started. This supplemental
failure is retained and excluded; it does not alter the 13 successful primary
cases, whose sources all maintain their intended rates. Host-daemon CPU remains
an unqualified measurement gap, and neither sampler's data supports a claim
that PipeWire itself has zero CPU cost.
