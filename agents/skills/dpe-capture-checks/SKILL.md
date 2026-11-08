---
name: dpe-capture-checks
description: Change or qualify DPE screenshare codecs, optimized capture backends, GPU buffer handoff, source lifecycle or capture performance using the native sender/receiver and owned workloads.
---

# Validate DPE capture and screensharing

Use [the screensharing guide](../../../docs/screensharing.md) to identify the
supported setting/backend and inspect its current implementation under
`integration/video/` and `integration/updater/capture-*.cjs`. Historical notes
explain experiments; current code, locks and manifests define shipped behavior.
Run commands from the repository root.

## Trace the entire path changed

Follow source discovery/permissions, picker selection, producer/hook, PipeWire
buffers, native Discord capture, encoding, packets and receiver presentation.
Keep app-audio selection separate from video-source identity. Do not infer a
working stream from a picker preview or a producer's frame counter.

For GPU work, verify DMA-BUF plane/modifier contracts, acquire/release fences,
ownership and bounded buffer reuse. Preserve synchronization across relay/copy
fallbacks, cursor composition and FPS changes; producer allocation mode can
remain fixed even when a consumer changes handoff mode. Do not weaken fences to
make a test producer faster. Check source loss, cancellation and shutdown cleanup.

When native offsets or ABI change, use
[the native patch workflow](../dpe-native-patches/SKILL.md). Rebuild the changed
helpers and verify the complete capture bundle through
`scripts/stage_capture_backends.py`. Preserve its file inventory/hashes and stage
coherent candidate bundles; do not mix a new producer with stale hooks.

## Choose observable checks

- Use `scripts/check_screenshare.py --help` for bounded native sender/receiver
  cases. Cover the affected encoder and actual decoder, dimensions/cropping,
  unique decoded frames, quality, latency and cleanup. A hardware encode pass
  with an incompatible software receiver does not qualify delivery.
- Use [matched benchmarks](../../../docs/agent/capture-benchmark-workflow-20261102.md)
  for `scripts/benchmark_capture_methods.py` and
  `scripts/benchmark_capture_lifecycle.py`. Their dry-run plans expose identities;
  `--run` starts the bounded owned workload. Use fresh outputs and verified inputs.
- For source lifecycle changes, exercise source loss/recovery, resize/fullscreen,
  pause/resume, 60→30→60 FPS, cursor and fallback transitions where relevant.
  Require post-transition unique-frame delivery and clean shutdown.
- Run affected native/JS regressions, compile checks and the real settings
  harness for settings changes. Finish with repository lint and tests.

For performance comparisons, keep content, actual source/output dimensions,
encoder, bitrate, receiver, delivery mode and pressure equal. Alternate order,
repeat and retain failures. Distinguish submitted/polled frames from unique
decoded frames, bandwidth estimates from media bitrate, and process/DRM counters
from device-wide utilization. A synthetic shader is not proof of game or live
internet-viewer performance.

Capture only owned synthetic workloads; save reports and counters rather than
user content. Do not install privileges, reconfigure displays, kill unrelated
apps or use account calls as an unattended test. Record unsupported GPU,
compositor and receiver combinations explicitly.
