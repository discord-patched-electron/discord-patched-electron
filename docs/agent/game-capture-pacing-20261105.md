# Game capture pacing and remaining CPU — 5 November 2026

The bundled game hook now tolerates small presentation-time jitter around its
capture deadline. A game running at the requested share rate previously lost
valid frames when they arrived fractionally early. The fix improves delivered
FPS and CPU per delivered frame; it does not reduce total CPU at the cost of
missing frames. It follows the [direct GPU path](direct-gpu-capture-20261105.md)
and [encoder import cache](encoder-import-reuse-20261105.md).

## Cause and bounded fix

The hook used the strict `dpe_capture_rate_due` clock. In a deterministic
20-second test, a 60 FPS source with a repeating −0.4 to +0.35 ms timing variation
passed only 720 of 1,200 presentations. The new helper accepts all 1,200.

`dpe_capture_rate_due_with_slack` admits a frame up to the smaller of 1 ms and
one eighth of an interval before its deadline. Admission advances the existing
deadline by one interval, retaining phase rather than building credit. Repeated
timestamps cannot consume another frame. A missed deadline never queues a
catch-up burst. One frame can cross a measurement-window boundary early; that
does not accumulate over time. Saturated timestamps cannot repeatedly trigger
early admission.

The bundled hook uses the new helper. The PipeWire adapter already implemented
the same tolerance; it now shares the helper and gains the saturation guard.
Stock OBS compatibility retains the strict clock. Held-frame exclusion, GPU
completion, explicit fences, lease acknowledgements, encoder configuration and
bandwidth adaptation are unchanged.

Sanitizer regressions exercise jitter at 30/60 FPS, duplicate timestamps,
30–600 FPS sources, 4,000 FPS sources against 60/240 FPS budgets, reset and clock
saturation. The hook handoff regression admits a presentation 0.67 ms early
while still requiring GPU completion and the exact consumer acknowledgement
before reuse.

## Matched native measurements

Evidence is retained in `out/capture-next-20261105/`. Three pairs alternate
baseline/candidate order. Every case uses the same frozen encoder/helper build,
an owned GLX 1920×1080 source capped at 60 FPS, relay allocation with the GL
semaphore bridge, direct GPU transport, H.264 VAAPI, requested 1080p60/6 Mbps,
normal delivery and the native Discord sender/receiver with Electron decoding.
Each capture lasts 20 seconds; FPS uses unique decoded markers in the steady
3–18 second interval, while resource samples use the harness's steady window.
The complete baseline and candidate producer/hook bundles are independently
verified rather than mixing hook generations.

| Pair | Previous unique FPS | Fixed unique FPS | Previous CPU % | Fixed CPU % |
| ---- | ------------------- | ---------------- | -------------- | ----------- |
| 1    | 53.87               | 57.53            | 55.86          | 57.34       |
| 2    | 46.33               | 57.53            | 48.84          | 56.55       |
| 3    | 47.00               | 57.53            | 49.57          | 57.33       |

CPU percentages are fractions of one CPU core, summed across the native local
sender/receiver, helper, source and compositor. They include receiver readback
and fixture overhead. These are not sender-only resource measurements.
Existing host services outside the monitored groups are omitted; these are
sampled pipeline totals, not complete session/PC CPU. See the
[current comparison audit](current-pipewire-comparison-20261105.md).

Median unique FPS rises from 47.00 to 57.53, about 22%. Total CPU rises from
49.57% to 57.33% of one core because more frames are delivered. The median ratio
of whole-loop CPU to unique delivered FPS falls from 10.54 to 9.96 ms/frame,
about 5.5%. That ratio includes fixed source/receiver costs and is not a direct
measurement of CPU spent on a single sender frame. Baseline FPS varies with
deadline phase; all three fixed runs deliver the same steady unique count.

| Other median resource metric        | Previous | Fixed  |
| ----------------------------------- | -------- | ------ |
| Native process-tree CPU, one core % | 37.13    | 44.53  |
| Native DRM encoder engine, client % | 11.48    | 14.02  |
| Summed mean RSS, MiB                | 1517.7   | 1518.4 |
| Observed media bitrate, Mbps        | 5.94     | 6.24   |

Encoder utilization increases with the restored frames. DRM client counters
are not device-wide utilization; summed RSS double-counts shared mappings and
does not measure physical memory consumption. Configured bitrate limits remain
the same, with the normal controller determining observed media bitrate.

Median capture-to-receiver latency remains 20 ms, with a 22 ms p95. The fixed
producer supplies approximately 60 frames per second; the remaining gap to
60 decoded FPS is downstream of producer pacing. The WebRTC frame dropper and
bandwidth control remain enabled.

All six cases pass actual encoding, decoding, dimensions, picture markers,
cancellation and shutdown. Every case reports zero direct video CPU readbacks,
zero direct-path failures and zero live encoder imports after teardown. The
fixed runs use four imported encoder surfaces and reuse them; they do not
reintroduce a PipeWire video stream.

## Remaining CPU attribution

A separate 20-second KMS/direct-GPU run samples `/proc` task CPU counters at
250 ms intervals, with the same native local receiver. The capture worker now
sets its thread name once at startup so its counters can be identified. The
steady 3–17 second samples give these approximate costs:

| Thread                     | CPU, one core % |
| -------------------------- | --------------- |
| GPU process main           | 16.31           |
| Native incoming video      | 9.04            |
| Renderer main              | 5.29            |
| Shared native voice        | 4.04            |
| Native video encoder queue | 1.47            |
| Direct capture worker      | 0.44            |

The incoming-video thread belongs to the local receiver. GPU-process,
renderer-main and shared native work cannot be cleanly charged to sender or
receiver from these counters. Driver work, deferred threads and GPU time are
not all charged to the named capture/encoder threads. This profile therefore
does not establish an exact sender-only CPU cost, nor prove that another
encoder micro-optimization will reduce overall CPU materially. Separating the
native sender and receiver, or collecting function-level samples, would be
needed for that attribution.

`perf` attachment was denied by the current kernel permissions. No kernel or
capability setting was changed. The retained `profile-final/thread-summary.json`
contains task counters rather than a call-stack profile. Profiled-run resource
totals are excluded from the matched comparisons above.

## Qualification and limits

A single high-source-rate control pair, capped at 600 FPS, delivers 57.20 versus
57.40 unique FPS and uses 71.73% versus 71.89% of one CPU core. Both have 20 ms
median latency. This small control does not establish an efficiency difference;
it checks that the matching-rate fix does not materially change the already
budgeted fast-source path. A separate fixed 30 FPS share from a 60 FPS source
delivers exactly 30.00 steady unique FPS, with 601 converted/submitted frames
over the complete 20-second session, zero readbacks and zero live imports.

The fixed lifecycle case passes all 16 checks: 60→30→60 rate changes,
fullscreen resize/restore, source pause/recovery, post-transition unique-frame
delivery and clean producer/native shutdown. It submits 1,037 GPU frames with
five imports and 1,032 cache hits, zero video CPU readbacks and zero live imports
after teardown. Same-size graphics-context recreation is not covered by this
lifecycle fixture.

Physical checks use the RX 7900 GRE, Mesa 26.2.5 and Niri. This synthetic source
uses GLX; it does not qualify Vulkan games, other drivers, older machines or live
Internet viewers. The change recovers valid presentations rather than changing
image quality or encoder rate control. No privilege configuration, display mode,
installed app or account call changed.

## Repository and bundle checks

Repository lint passes. The full suite passes 748 Python tests and 447
JavaScript tests, with three JavaScript skips. This includes the pacing,
GPU-handoff and direct-worker regressions.

The final coherent `capture-final` bundle verifies all 170 files. Its binaries
are byte-identical to the measured candidate; the sole corresponding-source
difference is the capture worker's diagnostic thread name. The measured frozen
native helper already includes that name. `qualification.json` checks all 11
final native cases, their helper identity, import accounting, zero video
readbacks, absence of PipeWire video streaming and teardown. The initial
exploratory `profile` directory is retained separately; it is not a matched
measurement or one of these final cases.
