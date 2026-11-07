# Optimized capture efficiency — 5 November 2026

The optimized producers did **not demonstrate a substantial reduction in CPU or
GPU board power** in this workload. Their main measured benefit is frame
freshness under GPU contention; game relay also reduces local latency. These
are local native sender/receiver measurements on one AMD/Niri machine, not
sender-only Discord resource usage or real-game FPS-loss measurements.

The retained matrix contains **54 capture cases and four uncaptured baselines**,
plus an excluded setup smoke. All 54 pass the native setup/delivery gates;
passing those gates does not imply that a case delivers the requested 60
unique FPS. `qualification.json` verifies actual source dimensions/rate,
accelerated encoding, native GPU imports, cleanup and consistent helper,
receiver and transport-adapter hashes.

Evidence lives in `out/capture-benchmark-20261105/`. All tables exclude the
quarantined overlap batch and the initial smoke. Values are medians of case
summaries; parentheses show the minimum–maximum across repeats, not confidence
intervals. Latency p95 is the median of each run's p95, not a pooled percentile.
CPU includes the native sender, local receiver/readback, capture helpers,
synthetic source and compositor. Pressure workers are excluded.
Accounting clarification: the process-tree sampler omits existing host services
outside those groups, including the host PipeWire daemon and Xwayland server.
These totals are sampled pipeline CPU, not complete session/PC or sender-only
CPU; see the [current comparison audit](current-pipewire-comparison-20261105.md).

## Production cadence, 600 FPS source

Three repeats per method; requested 1920×1080/60, H.264 VAAPI, 6 Mbps. Portal
uses its ordinary cadence; the optimized paths use event-driven fast delivery.
Both game handoffs request the GL bridge, but only direct/relay hook
allocation activates it. Forced copy retains the legacy GLX allocation and
completion path. These are whole-path comparisons, not an isolated-copy
ablation. The broker normally selects automatic supported relay/fallback.

| Method            | CPU, one CPU %   | Unique FPS       | CPU ms / unique frame | Latency p50 / p95, ms |
| ----------------- | ---------------- | ---------------- | --------------------- | --------------------- |
| Portal / PipeWire | 74.0 (73.7–74.4) | 59.7 (59.7–59.7) | 12.4                  | 41 / 44               |
| Game copy         | 73.0 (72.5–73.7) | 59.9 (59.9–60.0) | 12.2                  | 54 / 55               |
| Game relay        | 74.9 (74.4–75.8) | 60.0 (59.8–60.0) | 12.5                  | 35 / 37               |
| Direct KMS        | 76.6 (75.8–77.7) | 59.9 (59.9–60.0) | 12.8                  | 56 / 58               |
| Direct Wayland    | 75.6 (75.6–76.6) | 60.0 (59.9–60.0) | 12.6                  | 51 / 54               |

| Method            | Native + helper CPU % | GPU board busy % | GPU board W      | Summed pipeline RSS, MiB | Measured media Mbps |
| ----------------- | --------------------- | ---------------- | ---------------- | ------------------------ | ------------------- |
| Portal / PipeWire | 46.2                  | 19.4             | 69.1 (69.0–69.4) | 1244                     | 6.20                |
| Game copy         | 48.2                  | 19.6             | 69.3 (69.2–69.3) | 1442                     | 5.77                |
| Game relay        | 47.3                  | 19.5             | 69.1 (69.1–69.1) | 1435                     | 5.97                |
| Direct KMS        | 50.4                  | 19.9             | 69.2 (69.0–69.3) | 1425                     | 5.77                |
| Direct Wayland    | 47.7                  | 19.8             | 69.2 (69.0–69.2) | 1400                     | 5.98                |

The native group alone consumes most of the measured application CPU; the
receiver/readback is part of that group. The capture-helper delta is small in
absolute CPU time. The native encoder engine remains about 14.3% active across
these methods. Do not compare that engine percentage directly with graphics
engine or whole-board busy percentages.

Summed RSS is higher for the optimized pipelines, but shared mappings can be
counted several times. It does not establish an equal increase in physical RAM.
Board VRAM/GTT readings are retained in `summary.json`; they include unrelated
session allocations and are not per-capture memory attribution. No CPU-package
power or whole-PC energy measurement was made. Small CPU differences also
coincide with differing actual bitrates, despite the same requested limit;
these results do not establish efficiency at identical objective image quality.

## GPU contention, normal cadence

Three repeats per method; the table uses only the constrained phase. This
controls native cadence to compare capture producers and does not independently
qualify production fast delivery under pressure.

| Method            | CPU, one CPU %   | Unique FPS       | CPU ms / unique frame | Latency p50 / p95, ms |
| ----------------- | ---------------- | ---------------- | --------------------- | --------------------- |
| Portal / PipeWire | 75.4 (75.4–76.4) | 55.3 (53.8–59.3) | 13.6                  | 49 / 60               |
| Game copy         | 75.9 (74.9–76.7) | 60.0 (59.8–60.0) | 12.7                  | 68 / 70               |
| Game relay        | 77.0 (76.1–80.7) | 60.0 (60.0–60.0) | 12.8                  | 47 / 49               |
| Direct KMS        | 76.9 (76.2–77.1) | 60.0 (60.0–60.0) | 12.8                  | 69 / 72               |
| Direct Wayland    | 76.2 (76.0–77.0) | 60.0 (60.0–60.0) | 12.7                  | 64 / 68               |

Portal's constrained unique throughput ranges from about 54 to 59 FPS, while
its native input/encoding counters stay near 60. Two runs contain 35 and 44
repeated decoded picture markers. The optimized producers preserve approximately
60 distinct frames per second in this workload. This supports a freshness and
useful-throughput benefit under the exercised contention, rather than a large
reduction in absolute CPU cost. It does not identify the exact compositor/GPU
scheduling cause of the repeated pictures.

## Source rate equal to the share rate

Two repeats per method at a 60 FPS source, with normal native delivery. Game
copy here is the legacy bridge-disabled control; relay uses the bridge.

| Method            | CPU, one CPU %   | Unique FPS       | CPU ms / unique frame | Latency p50 / p95, ms |
| ----------------- | ---------------- | ---------------- | --------------------- | --------------------- |
| Portal / PipeWire | 59.8 (59.3–60.3) | 59.1 (59.1–59.1) | 10.1                  | 56 / 58               |
| Game copy         | 57.9 (57.8–58.0) | 30.0 (30.0–30.0) | 19.3                  | 96 / 97               |
| Game relay        | 59.3 (59.1–59.5) | 59.9 (59.9–60.0) | 9.9                   | 42 / 44               |
| Direct KMS        | 61.4 (60.9–61.8) | 59.9 (59.9–60.0) | 10.2                  | 72 / 74               |
| Direct Wayland    | 61.7 (61.4–62.1) | 59.8 (59.6–60.0) | 10.3                  | 74 / 76               |

Current forced-copy audit, with fast delivery and the GL bridge requested
(the bridge remains inactive for forced-copy allocation):

| Method    | CPU, one CPU %   | Unique FPS       | CPU ms / unique frame | Latency p50 / p95, ms |
| --------- | ---------------- | ---------------- | --------------------- | --------------------- |
| Game copy | 37.1 (36.9–37.3) | 30.0 (30.0–30.0) | 12.4                  | 102 / 104             |

The legacy-copy control encodes near 60 FPS but displays only about 30 unique
FPS, with roughly 595 repeated markers in a run. Its apparently similar CPU
cost therefore buys half the useful frames. This is not the automatic relay
path's result. The current forced-copy audit also delivers only 30 unique
FPS: its producer submits 600 frames over 20 seconds, the native sender encodes
about 598, and the fast delivery counters show about 599 fresh frames. Its lower
CPU reflects half-rate useful throughput rather than equivalent 60 FPS work.

Fast delivery still removes wasted work in that half-rate copy case: total
CPU falls from 57.9% to 37.1% of one logical CPU (about 36% lower), with roughly
600 distinct frames delivered in either run. The native encoder processes
about 598 frames instead of about 1,195 repeated-inclusive frames. That is a
useful repeat-filtering improvement, but it does not make forced copy a working
60-unique-FPS producer. The earlier and later runs are separate blocks, not
simultaneous power/CPU measurements.

Source review points to the GLX compatibility completion path: a GL fence is
polled at a later game presentation, then the single exported lease remains
held until the producer's GPU copy is acknowledged. A capture every other
presentation is consistent with the measured half rate at a 60 FPS source.
Detailed fence/ack timestamps were not instrumented, so that final scheduling
explanation remains an inference; the producer-stage throughput limit is
directly observed. This is a priority follow-up for GLX copy/fallback behavior.
No fence, release contract or production capture behavior was changed.

## Normal-cadence, high-source-rate control

Three repeats for the four original producers. Direct Wayland's high-source-rate
quiet comparison is covered by the production-cadence table instead.

| Method            | CPU, one CPU %   | Unique FPS       | CPU ms / unique frame | Latency p50 / p95, ms |
| ----------------- | ---------------- | ---------------- | --------------------- | --------------------- |
| Portal / PipeWire | 75.4 (64.9–75.7) | 58.7 (45.5–60.0) | 12.8                  | 43 / 55               |
| Game copy         | 74.4 (73.6–86.5) | 59.9 (59.7–60.0) | 12.4                  | 63 / 64               |
| Game relay        | 78.5 (75.2–89.3) | 59.8 (59.4–60.0) | 13.2                  | 44 / 45               |
| Direct KMS        | 79.8 (77.1–82.3) | 59.6 (59.1–59.6) | 13.5                  | 64 / 68               |

The slow portal repeat (`600-none-r3-portal`) remains in the table. The capture
adapter receives 1,198 frames in 20 seconds, while the native sender records
277 rate-limiter discards and only 921 encoded frames. Unique throughput in the
analysis window is 45.5 FPS and recovers later. Its lower CPU is not an
improvement in capture efficiency. The pressure repeats' repeated pictures have
a different signature: near-full encoded throughput and no rate-limiter drops.

## Uncaptured source baselines

| Source cap | Source + compositor CPU % | GPU board busy % | GPU board W | Observed submitted FPS |
| ---------- | ------------------------- | ---------------- | ----------- | ---------------------- |
| 600        | 21.7                      | 16.2             | 58.1        | 600.0                  |
| 60         | 8.7                       | 5.2              | 53.0        | 60.0                   |

For the 600 FPS source, the instrumented local round trip adds approximately
52–55 percentage points of one logical CPU and 11 W of GPU board power over
the uncaptured source. That increment includes the local receiver/readback and
is not sender-only screensharing cost. The optimized paths do not materially
reduce that increment in these measurements.

These separate runs show the cost of displaying and rendering the owned source
without capture, encoding or a receiver. Subtracting them from a round-trip
case gives an approximate local experiment increment, still including the local
receiver and readback. It is not the extra CPU consumed by a sender-only app.
All capped cases report actual source dimensions and submitted FPS; maintaining
a 600 FPS cap does not measure the loss of an uncapped real game's peak FPS.

## Why the methods differ

- Portal reuses Niri's capture producer. Its PipeWire rate negotiation is already
  60 FPS in these cases; it is not blindly processing every 240 Hz display frame.
- The GL hook still blits the application backbuffer into exported storage.
  Relay removes an additional producer handoff copy, not every GPU copy in the
  complete render/capture/encode path. Fenced ownership and releases remain.
- Current fast delivery is event driven. A 240 FPS native request does not imply
  240 expensive callbacks per second: the retained delivery counters show about
  1,200 fresh/forwarded frames over 20 seconds, with few duplicates, at 60 FPS.
- KMS performs a GPU copy into owned buffers and uses the existing privileged
  helper. Direct Wayland uses the compositor's image-copy protocol. Both still
  hand DMA-BUFs through PipeWire to the same native encoder.

## Practical decision and remaining work

For minimum measured resource use alone, the results do not justify replacing
portal capture with an optimized producer on this machine. For an eligible game,
relay provides the strongest combination of lower local latency and fresh-frame
throughput under the exercised pressure, at comparable resource use. Direct
monitor capture preserves fresh throughput in the pressure test, but does not
show a large resource saving or relay's latency result here.

The next resource-focused experiment should put sender and receiver in separate
processes, then repeat with owned Vulkan/native-Wayland workloads, real game
logic and CPU/graphics/memory contention. That would measure sender-only CPU,
proportional memory, actual uncapped game FPS loss and longer-term resource
behavior. This pass does not measure CPU contention, Vulkan hooks, other GPUs,
AV1/HEVC, 1440p/4K, 30/120 FPS sharing, WAN viewers or matched objective image
quality. Production fast cadence under GPU pressure remains a separate test gap.

## Measurement contract

The owned SDL3/OpenGL workload renders moving frame markers at 1920×1080 on
DP-2 (240 Hz, scale 1, normal transform). The source uses XWayland/GLX at either
60 or 600 submitted FPS. Every share requests 1920×1080/60, AMD VAAPI H.264,
6 Mbps and the same local Electron receiver. Each capture lasts 20 seconds;
primary summaries exclude the first and last two seconds. Dimensions, decoded
markers, sender statistics and producer shutdown are retained. No desktop
images, video, account logins or calls are saved or performed.

The host is an i7-12700K and AMD Navi 31/RX 7900 GRE, running Mesa 26.2.5,
PipeWire 1.6.10 and Niri. `environment.json` retains the exact kernel, package
versions, monitor inventory, repository status and source hashes. Each
`plan.json` identifies the exact executable/module/library inputs. The native
module and Electron runtime are reused from the existing managed dev version;
the complete capture bundle was rebuilt into a separate directory and verified
by `scripts/stage_capture_backends.py`. No installed application is updated.

The portal case uses Niri's Mutter-compatible ScreenCast API, which supplies
its portal capture backend, excluding picker interaction. Game, direct Wayland
and KMS producers also deliver DMA-BUFs through PipeWire to Discord. This
compares producers and handoff work rather than removing PipeWire transport.

Normal cadence is a control that disables the custom source's production
fresh-frame delivery. The production-cadence comparison retains normal portal
delivery and enables fast delivery for the optimized paths. The initial
normal-cadence copy control disables the GL bridge request; the later copy
and relay comparisons both request it. Workload logs confirm activation only
for relay: forced copy does not request the direct hook allocation needed to
activate the bridge. Both sets of copy-versus-relay comparisons include
synchronization/hook-path differences and cannot isolate one copy's cost.
Forced copy is a diagnostic mode; the broker normally selects automatic handoff
with supported relay and safe fallback.

Three high-rate normal-cadence repeats cover portal, game copy, game relay and
KMS. Three GPU-pressure repeats and two 60 FPS source repeats cover all five
producers. Three production-cadence repeats cover all five producers at the
600 FPS source cap. Two additional forced-copy cases audit fast delivery at
the 60 FPS source cap. Method order varies within the primary matrix and the production comparison. Direct Wayland's
pressure and lower-rate cases run in a later block, so their cross-method
comparison has more exposure to changes in background desktop activity. GPU pressure is the
fixture's synthetic compute workload, with initial/constrained/recovery phases.
It is not a real game, and does not establish performance under graphics,
VRAM-capacity or memory-bandwidth saturation.

An early follow-up orchestration error started three waiting runners together.
All of those follow-up artifacts are quarantined under `invalid-overlap/`, with
an explicit invalidation record. They are excluded from every table and
conclusion. The replacement follow-up uses an exclusive process lock and runs
each case sequentially. The original 32-case matrix completed before the
overlap and remains valid.

## Reading resource results

- CPU uses one logical CPU as 100%. It sums observed process CPU-time deltas,
  including descendants spawned from non-main threads. Whole-pipeline totals
  include the source workload and compositor; pressure workers are separate.
  Hook work executes inside the game, so excluding the workload would hide cost.
- The native group includes the sender, local receiver, rendering and marker
  readback. Its absolute CPU/RSS is not sender-only Discord overhead.
- Compositor CPU includes other session work. Uncaptured source baselines help
  estimate incremental cost but are separate runs, not an exact subtraction of
  simultaneous background activity.
- DRM engine counters measure process-client activity. They deduplicate client
  IDs within each group and do not represent whole-device utilization or power.
  Root-owned KMS helper descriptors may be unreadable to the sampler.
- RSS is the sum of simultaneous resident-page totals. Shared pages can count
  more than once; it is neither proportional/private memory nor GPU allocation.
- `device-resources.jsonl` adds board-wide power, GPU/memory busy, VRAM and GTT
  use for later cases. These readings include the whole desktop. Early cases
  predate this sampler; missing values are not zero.
- Unique decoded frames measure useful throughput. Capture callbacks, native
  input FPS and encoding FPS alone do not establish viewer throughput.
- Marker latency ends at local receiver readback. It includes capture, encoding,
  local transport, decoding and readback, but excludes a WAN/SFU and physical
  monitor scanout. No internet viewer or Windows comparison is claimed.
- Six Mbps is the requested configuration, not a guaranteed measured media rate.
  Native startup adaptation can change both output bitrate and delivered FPS.
  Low CPU with fewer frames is a throughput tradeoff, not an efficiency win.
  CPU milliseconds per unique frame are a window-level estimate: resource
  sampling and receiver arrival clocks have a small startup offset, rather than
  a frame-by-frame attribution.

## Reproduction and artifacts

Follow [the maintained benchmark workflow](capture-benchmark-workflow-20261102.md).
`run-matrix.py`, `followup.py` and `baselines.py` retain the exact case sequences;
`matrix.json` and `followup.json` retain commands, exits and elapsed times.
`analyze.py` derives `analysis.json` from the retained reports and counters.
`environment.py` and `device-sampler.py` retain host and device observations.
These generated scripts remain under `out/`; they are investigation artifacts,
not installed application components.

Per-case evidence includes `plan.json`, `native/report.json`, `resources.json`,
`resource-summary.json`, `resource-phases.json`, `workload.log`,
`producer-events.json`, `cleanup.json` and `result.json`. Failures and throughput
outliers remain in the results. Check the decoded dimensions and actual handoff
before comparing a case. No result qualifies another GPU, compositor, graphics
API, codec, resolution or frame-rate target.

## Repository validation

The benchmark runner now supports direct Wayland with an explicitly hashed
helper and reports simultaneous mean/peak process RSS. Regression checks cover
helper arguments, dry-run input identities, bridge-request guards, RSS units,
missing counters, simultaneous totals and existing PID/DRM deduplication.

`bun run lint` passes. `bun run test` passes: 744 Python tests and 443 JavaScript
passes, with three JavaScript skips. Evidence: `lint.log`, `tests-final.log` and
`qualification.json` under the artifact directory. No production capture
implementation, installed app, desktop configuration or permission was changed.
