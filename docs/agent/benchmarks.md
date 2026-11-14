# Benchmarks and reproducible evidence

Results here describe fixed historical inputs. Check current locks/manifests before
reusing a runtime or drawing a current performance conclusion. Keep measurements
with method, hardware, scopes, raw artifact location and remaining limits; raw
logs belong in `out/` or [saved evidence](../evidence/), not new dated Markdown.
An old local path is not a promise that its artifacts still exist.

## Reproduction

`scripts/benchmark_capture_methods.py` compares the actual native sender/local
Electron receiver using one owned fullscreen synthetic source. Default execution
is a dry run recording options/input hashes; `--run` starts a bounded case.
Compile the workload from `tests/screenshare/pipeline-workload.c` using SDL3/GL
and provide verified `--runtime`, `--voice`, `--openh264` (for H.264),
`--workload`, `--render-node`, `--monitor` and a fresh `--output`. Inspect current
`--help` rather than reusing private machine paths.

Compare `--method portal|game|wayland|kms` with coherent helper/bundle options.
Direct hardware game/KMS cases use `--capture-transport direct-gpu` and the same
bundle's `--direct-helper`. Keep `--delivery normal|probe|fast` identical across
methods; production fast cadence needs its own qualification. Confirm actual
handoff/GL visibility activation and zero hidden readbacks in reports, rather
than assuming requested flags took effect. Keep actual source/output dimensions,
codec, bitrate, receiver and pressure identical. H.264 1080p and AV1 720p are
separate comparisons. Rotating/interleaved order with at least three repetitions
is needed for a claimed win; retain failures rather than averaging passes only.

Reports retain input hashes (`plan.json`), native markers/dimensions/latency and
adaptation (`native/report.json`), source FPS, producer/teardown events, resources,
cleanup and overall outcome. CPU uses one core=100%. Sample CPU/RSS/DRM at 0.5 s;
PSS defaults to 2 s and phase changes because smaps walks are costly. Missing PSS
is absent, not zero/repeated; retain coverage and observer thread cost. Summed
RSS can double-count shared pages; DRM engine counters are not whole-device power.
Include owned game/compositor work because hook cost runs in the source process.
An idle baseline and independent sender/receiver scope are essential for attribution.

`benchmark_capture_lifecycle.py` adds rate/resize/fullscreen/pause transitions and
requires actual changed decoded dimensions, resumed unique frames and cleanup.
Do not use a non-resizable source or fixed output override to satisfy resize.
`benchmark_gsr_capture.py` measures GSR KMS versus its portal route with an owned
source and discards encoded output to `/dev/null`. It uses existing authorization,
not installed privileges. GSR recording-loop counts and a clean exit are not
unique decoded pictures, image correctness or end-to-end latency. Its recorder
CPU cannot be compared directly to DPE sender-plus-receiver CPU. There is no
qualified matched DPE-versus-GSR power or older-hardware result.

## Native12 resource comparison

Nine alternating-order runs compare the current methods as shipped: PipeWire,
raw KMS and raw game capture. Each has a ten-second idle baseline and 30 seconds
of active capture. The owned 1920×1080 scene renders at 240 FPS; the independent
native receiver gets hardware AV1 at 1280×720, 60 FPS and a 6 Mb/s target.
Native media counters confirm approximately the requested bitrate. The same
runtime, decoder, content, dimensions, bitrate and inherited priority are used.
Order is PW/KMS/game, game/KMS/PW, then KMS/PW/game. All nine pass.

The machine is an i7-12700K with 20 logical CPUs, about 32 GB RAM, AMD amdgpu
Radeon RX 7900 GRE (RADV NAVI31), PCI `1002:744c`, Mesa 26.2.5,
kernel `7.3.1-1-cachyos`, and Niri/Wayland. DP-2 reports
1920×1080 at 239.761 Hz. The compiled runtime is retained artifact
`41282631878`, SHA-256
`6d0e3db61278f7be23afbb0129361d6fe4d30c9547e7fe5d1248740b9af57cb5`.
This is an AMD synthetic local comparison, not an older-PC, Intel/NVIDIA,
real-game or internet-viewer qualification.

CPU values below are percentage points of **one CPU core**, after subtracting
each run's idle baseline. They are not percentages of whole-PC CPU usage.
The larger scope adds the owned scene and compositor; neither scope includes
the receiver, shared Chromium browser/GPU processes or host PipeWire/Xwayland
services. Shared Chromium work cannot be assigned reliably to the sender alone.
No C-versus-Rust CPU saving is inferred from this method comparison.

| Method      | Sender + helpers CPU increase | Observed range | Add scene + compositor | Relative change in larger scope | Receiver latency medians |
| ----------- | ----------------------------: | -------------: | ---------------------: | ------------------------------: | ------------------------ |
| PipeWire    |                         4.438 |    4.333–4.508 |                  7.612 |                        baseline | 31 / 33 / 24 ms          |
| KMS direct  |                         4.621 |    4.490–4.765 |                  7.009 |                           −7.9% | 22 / 23 / 22 ms          |
| Game direct |                         3.465 |    3.226–3.586 |                  6.761 |                          −11.2% | 18 / 18 / 18 ms          |

Game's sender/helper capture increment is 21.9% lower. KMS's is 4.1% higher,
with overlapping run ranges; this does not establish a sender/helper CPU win.
Actual active sender-plus-helper CPU, including idle overhead, averages
6.912 / 6.885 / 6.174% of one core for PW/KMS/game. KMS's measured benefit is
mainly less compositor work and latency. At 60 FPS cumulative encoder-queue
discards range from zero to two per run. Unique decoded marker counts are PW 1789/1770/1766,
KMS 1799/1798/1798 and game 1799/1797/1798 in 30 seconds. Native frame counters
and unique pixel-marker counts are kept distinct.

## Memory and GPU accounting

These are active proportional-memory averages in decimal MB. The privileged
KMS server's PSS is unreadable; its upper bound uses RSS instead of assuming zero.
The totals below use the same four groups as the larger CPU scope. They exclude
receiver/shared Chromium/host services and do not measure physical VRAM.

| Method      | Sender PSS | Helper PSS | Owned scene PSS | Compositor PSS | Counted total |
| ----------- | ---------: | ---------: | --------------: | -------------: | ------------: |
| PipeWire    |     118.18 |          0 |           29.25 |          41.27 |        188.70 |
| KMS direct  |     102.55 |  0.83–3.34 |           29.24 |          41.31 | 173.92–176.43 |
| Game direct |     101.78 |      16.25 |           35.42 |          41.30 |        194.74 |

KMS saves about 12–15 MB in these groups. Game's smaller sender allocation is
offset by its broker/producer and larger game allocation: the counted total is
about 6 MB higher. An overall game RAM reduction is not established.

Per-client DRM counters show essentially equal sender video-encode engine duty:
4.935 / 4.931 / 4.940% for PW/KMS/game. Sender conversion work changes engines:
PW reports 0.456% graphics; KMS/game report 0.788/0.385% compute. Compositor
graphics duty averages 3.347 / 3.082 / 3.119%. These are separate engine/client
counters, not additive whole-device utilization or physical allocation totals.
The comparison does not establish an overall GPU/power saving. Optimized
streams report no full-resolution CPU readbacks; bounded visible previews
have their separate thumbnail readback counters.

## Stress, throughput and priorities

The 240-FPS contention cases add 20 CPU workers and one GPU shader worker for
four seconds between initial and recovery phases. Measured CPU busy averages
98.3–98.6% over the allowed CPUs; GPU busy averages 91.9–93.1%, peaking at
98–99%. Source rendering stays about 240 FPS with no draw-fence timeouts.

| Method      | Constrained decoder FPS samples | Constrained median latency | Encoder-queue discards in run |
| ----------- | ------------------------------- | -------------------------: | ----------------------------: |
| PipeWire    | 238 / 240 / 238 / 239           |                      32 ms |                             7 |
| KMS direct  | 233 / 236 / 239 / 240           |                      31 ms |                             5 |
| Game direct | 169 / 177 / 172 / 155           |                      25 ms |                             4 |

All cases recover and clean up. Decoder counters are not proof of 240 unique
pictures; the pixel-marker observer itself has a sampling ceiling. Game's
single leased texture and explicit acquire/completion waits limit admission
under this load: 260 presents encounter an outstanding lease. The game itself
continues rendering at 240 FPS. Frames/fences are retained until safe release;
synchronization is not weakened to inflate FPS. Increasing source buffering
would trade memory and latency for pressure throughput; it is not presented as
a resource saving.

Separate **60-FPS** contention cases pass for all methods. KMS/game both report
zero capture drops, zero encoder-queue discards and zero full-resolution CPU
readbacks. Their constrained decoder samples are 59/60/60/60 FPS. PipeWire
reports 60/60/58/61 and one encoder-queue discard. This supports smooth normal
capture under the tested load, not a universal zero-drop guarantee.

Default scheduling remains inherited. The existing optional balanced policy
uses a bounded −2 niceness adjustment on capture work, with permission fallback;
it does not alter DE/game priorities, pin cores or use real-time scheduling.
Earlier three-pair priority results overlap, including a worse pair, so raising
priority is not the default. This process has no real-time priority allowance.
Libva exposes [context-priority negotiation](https://intel.github.io/libva/group__api__core.html),
but the local VPP, AV1 and low-power AV1 queries all return
`VA_ATTRIB_NOT_SUPPORTED`; no GPU-priority override was applied.

No new “minimal resources” preset is shipped without demonstrated benefit.
Demand-gated preview/capture and negotiated idle pause already avoid unused
work. The earlier three paired idle-game tests reduce scene-plus-helper CPU
from 5.315 to 3.872% of one core, with one bootstrap frame instead of roughly
2,400 unused captures in ten seconds. This is a waiting-state result, not a
steady-share improvement. The tested asynchronous encode-depth alternative
has insufficient repeated whole-pipeline evidence to change its default.

## Where time goes

An instrumented native KMS AV1 720p60 case records these mean microseconds per
call. It is separate from the uninstrumented resource comparison. Spans are
inclusive, may nest and may run on different threads; do not sum them.

| Stage                                 | Mean wall µs | Mean thread CPU µs |
| ------------------------------------- | -----------: | -----------------: |
| Producer KMS request                  |        86.13 |              14.53 |
| Producer metadata validation          |        22.70 |              22.44 |
| Producer frame send                   |         3.22 |               3.16 |
| Native receive                        |         9.19 |               8.59 |
| Acquire handling                      |         3.89 |               3.80 |
| Conversion, including nested VPP wait |       213.20 |              58.69 |
| VPP completion wait                   |       131.24 |               6.80 |
| Encoder send                          |       971.93 |             109.63 |

This run converts 722 frames and sends 720 GPU encoder inputs, with four imports,
716 cache hits, no CPU inputs, failures or import fallbacks. Kernel/GPU time is
not replaced with CPU wall time. Privileged exporter internals and device-wide
GPU allocation are not completely attributed by these spans.

### Native12/13 qualification identity

The nine-run resource series above belongs to Native12. Native13's SDR Vulkan
repair is a functional follow-up, not a repeated performance benchmark.
Actual Vulkan FP16 testing had failed before the repair despite GLX success.
The fixed owned Vulkan share delivered 594 frames in 9.89 s with changing hashes,
zero full-resolution CPU readbacks and no capture failures. GLX rerun delivered
591 in 9.78 s; reverse KMS delivered 314 at 768×432 in 10.43 s. These explicitly
authorized historical live checks do not authorize unattended calls now. The
retained runtime still has 1080→1082 AV1 display padding; later crop/clock ports
were not compiled into it.

Raw evidence: `out/live-capture-private/native12-normal-*`,
`native12-normal-summary.json`, `native12-accounting.json`,
`native12-profile-stages.json`, `native12-extreme-*`, `native12-pressure60-*`,
`native12-fallback-game-lifecycle1`, `native13-inputs.json`,
`native13-vulkan-source.log` and `native13-live-*` under that root.
Account traces remain ignored/private. There is no C-versus-Rust saving inferred
from this method comparison, and shared Chromium/host-service work is excluded
from the stated CPU/PSS scopes.

## Earlier H.264 comparison

5–6 November evidence in `out/capture-vs-pipewire-20261105/` used the same frozen
helper (`a600d7a785e92fdc9064894f6c41d779e04952655f27ac2c30c5256d6c016677`),
runtime/module/OpenH264/source inputs and normal delivery. GLX on Niri DP-2,
1920×1080/60 H.264 VAAPI, 6 Mbps, 20 s cases; three rotating repeats at source cap
600 FPS. Values are medians of per-case summaries, not pooled confidence intervals.

| Method          | Sampled CPU % of one core | Unique FPS | CPU ms/unique frame | Local latency p50/p95 ms |
| --------------- | ------------------------: | ---------: | ------------------: | ------------------------ |
| Portal/PipeWire |                     74.34 |      60.00 |               12.39 | 48 / 51                  |
| Direct game     |                     71.96 |      57.53 |               12.51 | 20 / 22                  |
| Direct KMS      |                     74.11 |      58.47 |               12.50 | 24 / 27                  |

This established lower local latency, not CPU-per-delivered-frame savings: direct
paths delivered fewer pictures and cost about 1% more per useful frame. A separate
two-pair source-60 block measured portal 61.98% CPU/51.70 unique FPS/12.10 ms per
frame versus game 57.11%/57.50/9.93; portal had repeated markers. That supports a
fixture-specific freshness benefit, not a universal PipeWire issue. KMS was not
included in that block. Source FPS and source dimensions matched.

Here CPU includes sender, local receiver/readback, source, helpers and compositor;
host PipeWire/Xwayland are excluded. Native/direct encoders differ despite equal
requested rate, so this is a complete-path comparison, not isolated transport or
equal objective quality. Daemon controls failed marker/dimension qualification
and were excluded, never silently added to totals. No board-power claim follows.

## Camera and Krisp

Physical Logitech UVC checks retained timing/dimension metadata only; no frames
were displayed/saved and fake audio replaced microphone capture. Two alternating
upstream/patched runs used the same native module and bundled Electron 44.9.3.

| Run | Upstream probe s | Patched probe s | Upstream first frame s | Patched first frame s |
| --- | ---------------: | --------------: | ---------------------: | --------------------: |
| 1   |            5.382 |           0.652 |                  7.770 |                 1.736 |
| 2   |            4.964 |           0.654 |                  6.047 |                 1.736 |

Both delivered 1280×720. The compiled Electron 46 alpha.1 comparison measured
8.37→2.33 s first frame and 5.42→0.66 s probing. With fast probing held on,
asynchronous init reduced the worst native engine heartbeat gap 1,130→25.3 ms
and first frame 3,455→1,739 ms. The fake connection sent only startup silence,
so sustained real-call voice continuity was not established.
[Physical metadata](../evidence/camera-wayland-local.json),
[compiled runtime](../evidence/prerelease-runtime-local.json),
[async metadata](../evidence/desktop-fixes-local.json).

Bundled native baselines measured 53.6 dB offline Krisp noise-only attenuation
with speech retained across Stable/PTB/Canary; these are distinct from compiled
runtime qualification. The actual off/on/off fixture, clean-reference thresholds
and [CI inputs](../evidence/native-ci.json) are in
[runtime validation](runtime-validation.md#baseline-measured-on-23-october-2026).

## Build reuse

A retained approximately 36 GiB workspace (6.4 GiB Ninja output) compressed to
13,456,173,452 bytes in 218 s using zstd level 3/two threads on the desktop.
Restoring file and directory nanosecond timestamps at the same physical path
produced Ninja `no work to do`; no compiler ran. A source-only checkpoint was
102,763,882 bytes. These are local archive/restore measurements, not hosted billing
or cross-version hit-rate proof. The initial successful alpha.1 build's final
resumed portion took 1,776 s; it is not a cold-build duration. Preserve native
inputs/provenance and [reuse bounds](releases.md#runtime-reuse).

## Limits to preserve

Local decoded-marker latency excludes WAN/SFU and physical scanout. Synthetic
shaders are not full game workloads. Encoding support does not prove hardware
decode. Missing engine counters, privileged-helper PSS and failed daemon controls
remain missing evidence. CPU, memory, GPU engines and power require separate
scopes; do not add nested stage spans or percentages across engines. Older
benchmark snapshots cannot establish latest-build performance, other vendors,
HDR, long sessions or current source-only port behavior. Replace a result when a
matched rerun supersedes it; do not append a task diary or duplicate its tables.
