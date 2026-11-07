# Reuse VAAPI encoder imports — 5 November 2026

The direct capture path now caches encoder-side imports of its NV12 buffers.
The previous backend created and destroyed a VAAPI surface for every submitted
frame, even when the converter reused the same allocation. The cache retains
at most four idle/in-flight import identities per encoder and releases each
producer frame lease at the original FFmpeg buffer-release point.

## Ownership and compatibility

`encoder-import-cache.h` keys each entry by the allocation's device/inode,
width, height, byte size, both offsets/strides and DRM modifier. A duplicated,
close-on-exec descriptor keeps that allocation identity alive; descriptor-number
reuse cannot produce a false cache hit. Active entries cannot be evicted.
If all four entries are busy, or stat cannot establish a usable identity/extent,
encoding uses the existing transient import path.

Each submitted frame still owns its separate producer lease. Cache reuse does
not acknowledge a producer early, retain a frame after encoding or weaken an
acquire/release fence. Closing or resizing an encoder retires its cache. The
cache retains the VAAPI device until its final in-flight reference disappears,
so delayed FFmpeg release cannot access a destroyed device.

The private version-1 encoder table and Discord C/Rust ABI remain unchanged.
An optional private diagnostic export supplies import/hit/eviction/live/bypass
counts through the existing direct-capture statistics. Unknown optional exports
remain optional. The pinned whole-module scopes still guard the native bridge.
The private build fingerprint includes the new header; package/updater source
staging already includes the complete video integration directory.

## Isolated CPU and correctness checks

Evidence is retained in `out/capture-import-20261105/`. The baseline helper and
private codec were frozen before editing. Candidate and baseline use the same
retained FFmpeg objects and codec settings; no Electron build was started.

Three alternating-order pairs encode 5,000 generated 640×480 H.264 frames per
case. The source changes between red and green every 30 frames. Thread CPU time
is measured around the private backend's NV12 submission call; GPU waits are
included in its wall time but not charged as thread CPU time. This is an
uncapped submission-cost experiment, not a 60 FPS application resource test.

| Metric, median of three runs           | Previous | Import cache |
| -------------------------------------- | -------- | ------------ |
| Encoder submission CPU, µs/frame       | 54.8     | 49.1         |
| Encoder submission wall time, µs/frame | 573.2    | 565.6        |
| Driver imports per 5,000 frames        | 5,000    | 1            |
| Reused imports per 5,000 frames        | 0        | 4,999        |

Submission CPU falls about 10%; the absolute median difference is about
5.7 µs/frame, equivalent to only 0.34 ms of CPU time per second at 60 FPS.
This must not be presented as a 10% reduction in total screensharing CPU.
All six H.264 output files are byte-identical (SHA-256
`e0e193a3c3c4dc257b8cdf8b306df5e94181895733d03b9163aac10fb746f21e`).
The original backend has no import counter; its 5,000-import count follows its
one-import-per-submission implementation. The candidate count is observed.

Separate H.264, HEVC and AV1 checks decode all 120 changing-color pictures
correctly while observing one import and 119 hits. This checks that cached
imports see subsequent GPU writes rather than stale pixels. Each case has
zero video CPU readbacks and zero live cached imports after teardown.

Sanitizer tests cover repeated hits, every layout field, descriptor-number
reuse, failed imports without descriptor leaks, full pinned caches, idle
eviction, and concurrent releases after encoder/cache closure. They open no GPU.

## Matched native checks

The matched harness uses frozen baseline/candidate helper builds, identical
producer/hook binaries from a complete verified bundle, and fresh profiles.
Each method has three pairs in alternating order: 16-second H.264 VAAPI runs,
an owned 1920×1080 source capped at 600 FPS, requested 1080p60/6 Mbps, normal
delivery cadence and the actual native local sender/receiver. Resources include
the local receiver/readback and source/compositor; they are not sender-only.
Existing host services outside the monitored groups are omitted; these are
sampled pipeline totals, not complete session/PC CPU. See the
[current comparison audit](current-pipewire-comparison-20261105.md).

| Method        | Total CPU, one CPU % | Native CPU % | Unique FPS | Latency p50 / p95, ms | Summed RSS, MiB | Media Mbps |
| ------------- | -------------------- | ------------ | ---------- | --------------------- | --------------- | ---------- |
| game previous | 72.23                | 44.83        | 57.64      | 19 / 22               | 1521            | 6.30       |
| game cache    | 72.53                | 44.44        | 57.64      | 19 / 22               | 1520            | 6.31       |
| kms previous  | 72.56                | 44.93        | 58.09      | 23 / 27               | 1468            | 6.34       |
| kms cache     | 72.02                | 44.62        | 58.00      | 23 / 26               | 1462            | 6.26       |

The full-pipeline CPU medians move in opposite directions for game and KMS.
These runs do not establish a consistent total CPU or physical-memory saving.
Native-process CPU medians are about 0.3–0.4 percentage points lower, but three
short pairs and overlapping CPU variation do not establish that entire delta
as the cache's effect. Summed RSS double-counts shared mappings. The isolated
submission measurements above give the more direct evidence of reduced work.

All 12 native cases pass actual hardware encoding, decoding, dimensions,
picture markers, cancellation and shutdown. Every candidate has zero video
CPU readbacks, no PipeWire capture stream, no cache bypasses and zero live
imports after teardown. Import creations plus cache hits reconcile exactly
with GPU encoder inputs. The H.264 58-FPS limitation remains; neither frame
dropping nor bandwidth adaptation was disabled to improve a reported number.

The candidate lifecycle case passes all 16 rate/resize/pause/recovery checks:
902 GPU submissions, six imports across encoder generations and 896 hits.
The native AV1 1280×720/60 case passes 1,201 GPU submissions with two imports
and 1,199 hits. Both finish with zero live imports and zero CPU readbacks.

The coherent bundle's full 170-file inventory and hashes are verified.
Frozen helper hashes and all per-case gates are retained in `qualification.json`;
`final-isolated-results.json` and `benchmark-summary.json` retain the final
isolated measurements. The earlier `isolated-results.json` is retained too.

## Limits

Physical checks use the RX 7900 GRE, Mesa 26.2.5 and Niri. Intel, NVIDIA,
other drivers and older CPUs have not been measured. The isolated fixture's
640×480 generated colors establish import reuse and exact output preservation;
they do not establish performance on game content or higher resolutions.
No privilege configuration, display mode, installed app or account call changed.

## Repository checks

Repository lint passes. The full suite passes 748 Python tests and 447
JavaScript tests, with three JavaScript skips. A reproducible rebuild of the first measured candidate was byte-identical.
The final compatibility guard adds an uncached fallback for descriptors without
usable stat information. The isolated pairs were repeated with that final build,
and `final-native` passes a fresh KMS sender/receiver case: zero CPU readbacks,
cache hits and zero live imports at teardown. The matched application table predates this compatibility-only guard.
A test-only stat shim then forced valid GPU inputs to appear uncacheable in the
final backend. H.264, HEVC and AV1 each encoded and decoded all 120 changing
pictures through the legacy path, reporting 120 bypasses and zero live cached
imports. The shim affects only the standalone test's named private codec DSO. `capture-final` verifies its 170-file
inventory and contains the current corresponding sources.
