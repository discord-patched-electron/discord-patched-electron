# Native12 capture qualification and resource comparison — 7 November 2026

The current `capture-latest` is Native16 with
[silent verified KMS maintenance and executable grants](kms-silent-maintenance-20261107.md).
The resource series below measures Native12; the subsequent Vulkan qualification
uses Native13. Native14's authorization change has separate live validation and
is not relabelled as a repeated resource benchmark.

At the end of the comparison, `capture-latest` and the dev profile used Native13. The clock profile uses
its byte-identical `capture-rust-clock6` copy. Native13 adds a Vulkan SDR export
repair after the Native12 resource series below. Its runtime, native bridge,
encoder, raw producer, KMS source, GLX hook and game consumers are byte-identical
to the measured Native12 components. Both optimized methods deliver
live video and sender previews without error 2011; KMS sharing also passes in
the reverse direction. All DPE-owned native helper implementations build from
Rust. Pinned upstream interposers/libraries, Electron C++ ports and C test
callers remain separate. [Build identities and launch checks](dev-capture-latest-20261106.md)
and the [complete investigation](capture-rust-profiling-20261106.md) retain the
intermediate failures, migration details and earlier comparisons.

## What the comparison establishes

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

## Final build and live checks

Native13 fixes a failure found by testing actual Vulkan separately from GLX.
The owned Wayland `vkcube` chooses `VK_FORMAT_R16G16B16A16_SFLOAT` with
`VK_COLOR_SPACE_SRGB_NONLINEAR_KHR`. Native12 rejects this SDR FP16 swapchain
before exporting any frames. The rejection and diagnostic rerun are retained;
the earlier successful GLX tests did not qualify Vulkan.

Rust now selects a known SDR export format at initialization. Eight-bit formats
retain their existing copy path; SDR FP16 and supported 10/16-bit normalized
formats use the upstream Vulkan blit into the existing eight-bit export image.
Source optimal-tiling and destination **actual modifier** blit features must
both be supported before allocation. The existing DMA-BUF, dedicated allocation,
acquire/release and single-lease checks remain in force. No extra export buffer,
CPU conversion or CPU readback is introduced. HDR/other color spaces and unknown
or integer formats remain unsupported. This is a functional repair, not a
measured FP16 efficiency claim. See Khronos's [format feature requirements](https://docs.vulkan.org/spec/latest/chapters/formats.html)
and [image blit rules](https://docs.vulkan.org/spec/latest/chapters/copies.html).

The packaged Native13 fullscreen Vulkan share delivers 594 receiver frames in
9.89 seconds. Four sampled owned-frame hashes differ, with nonconstant RGB
ranges; the preview advances 19→99 frames. Native telemetry reports 658 converted
frames, 641 GPU encoder inputs, one import plus 640 cache hits, zero capture
drops/failures, zero full-resolution CPU readbacks and zero preview errors.
The GLX rerun delivers 591 frames in 9.78 seconds, four changing frame hashes
and preview progress 20→95. Its cumulative telemetry adds 686 conversions and
651 GPU encoder inputs, with no capture drops, failures or readbacks.
Reverse KMS delivers 314 frames at 768×432 in 10.43 seconds with four changing
frame hashes. It adds 351 conversions and 347 GPU inputs, with no capture drops,
failures or full-resolution readbacks; that profile requests no local preview.
The first reverse hash probe selected a cross-origin avatar and stopped on a
canvas security exception, although the stream had delivered 65 frames. The
owned-scene probe now waits for and selects the actual larger stream. It saves
hashes/ranges only. All final calls disconnect and owned workloads exit.

Native13 passes fresh managed launch/relaunch, repository lint, 779 Python,
453 JavaScript (three skips) and 88 Rust tests. All three managed native payloads
match; the 526-file capture manifest hash is recorded in the
[managed build handoff](dev-capture-latest-20261106.md). The Native12 benchmark
series remains tied to its original inputs; the binaries it exercises are
unchanged in Native13.

Native12 passes fresh managed launch/relaunch, repository lint, 779 Python,
453 JavaScript (three skips) and 86 Rust tests. Native AV1/HEVC delivery,
H.264 game lifecycle and the Rust relay fallback's resize, fullscreen,
60→30→60, pause/recovery and teardown pass. The final relay fallback publishes
and releases 1,034 frames; exactly one graph-start retry occurs and none recur
in steady capture. Typed GPU owners and private Wayland server fixtures have
separate synthetic qualification.

Approved live checks use only the owned synthetic scene. Game sender preview
advances 10→102 frames in ten seconds, with 650 receiver frames; KMS sender
preview advances and the receiver reaches 736 frames. Reverse KMS reaches
384 frames at 768×432 in about 12.8 seconds. Both optimized methods use direct
GPU transport, without PipeWire capture callbacks, capture failures or
full-resolution CPU readbacks. Both accounts are disconnected afterward.
The receiver-profile identity mismatch was caught before a call; testing used
the account subsequently authorized by the user. No account names/IDs are
stored in tracked files.

The ignored private scripts now check both account identities, prewarm the
capture catalog, open only the named peer DM, bound waits and verify stream
progress. The single-response CDP client exits after flushing its response,
so a delayed WebSocket close cannot be mistaken for an evaluation timeout.
They send no messages and never automate verification challenges.

The retained runtime still has the known 1920×1080→1920×1082 AV1 decoding case;
later Electron crop/presentation-clock ports are not compiled into this build.
GSR's [reviewed source revision](https://repo.dec05eba.com/gpu-screen-recorder/commit/?id=f97d1e392816503282d20a0804c3ce3fd2943767)
was an architecture reference. Combined GPL capture binaries retain their
source/notices and [attribution](../../THIRD_PARTY.md). There is no matched
DPE-versus-GSR power or older-hardware result.

Private evidence is under `out/live-capture-private/`: `native12-normal-*`,
`native12-extreme-*`, `native12-pressure60-*`, `native12-live-*`,
`native12-fallback-game-lifecycle1`, `native12-normal-summary.json`,
`native12-accounting.json`, `native12-profile-stages.json` and
`native12-va-context-priority.json`. Reports contain counters and synthetic
markers; account automation traces stay ignored and private.
Native13 adds `native13-live-*`, `native13-vulkan-source.log`,
`native13-inputs.json`, `native13-desktop/` and `native13-full-tests1.log`.
