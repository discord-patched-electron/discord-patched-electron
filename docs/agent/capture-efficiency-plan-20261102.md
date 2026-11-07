# Capture efficiency execution brief — 2026-11-02

## Objective and scope

Make the selectable game and monitor capture alternatives measurably more
efficient than the existing GPU-buffer portal path in representative workloads.
Prioritize lower capture-to-receiver latency, uninterrupted delivered frames under
contention, and lower capture-attributable GPU/CPU work. Preserve truthful codec,
resolution and hardware diagnostics. Keep source selection in DPE settings; native
Discord source-picker work belongs in a separate UI pass.

This is the execution prompt for continuing implementation, not a performance
claim. Follow CONTRIBUTING.md and the current source/locks. Preserve existing
work and use independently built capture helpers with the retained Electron.
Use subagents for upstream research, hook review and benchmark orchestration;
coordinate hardware runs centrally so experiments do not contend with each other.

## Work order

1. Map the application-visible copies, queues, ownership and fences from game or
   scanout through actual native hardware encoding and local receiving. Consult
   OBS vkcapture, GSR and driver/API documentation. Count CPU readbacks separately
   from GPU copies; DMA-BUF transport alone is not a zero-copy claim.
2. Remove the game helper's intermediate BGRA copy where supported. Relay the
   hook's exported buffer and retain its lease until the consumer's actual GPU
   release. Negotiate explicit synchronization and validate failed fences,
   resize, cancellation and teardown. A returned PipeWire buffer alone is not
   proof of completed GPU reads. Preserve the safe copy path for unsupported
   layouts/synchronization; report which path actually ran.
3. Fold orientation into the hook's existing copy where possible, avoiding a
   separate flip pass. Keep admission before expensive work. Evaluate bounded
   slot reuse only if measured single-slot ownership limits throughput. Never
   block the game's present path waiting for a viewer, accumulate old frames,
   or add global hook injection.
4. Evaluate eliminating further conversion/transport stages using the native
   consumer's real accepted formats and lifetime contract. Prefer documented
   GPU interfaces and verified narrow adapters over assumptions about proprietary
   layouts. NV12 input or direct encoder surfaces must actually bypass a stage;
   adding a second conversion elsewhere is not an improvement.
5. Investigate a GSR-style KMS monitor backend: exported display buffers, recording
   cadence, synchronization, cursor/planes, modifiers, multi-GPU selection and
   hardware encoder interoperability. KMS cropping is not isolated window capture.
   Retain game capture unless a replacement supports its actual window semantics.
   Account for the privileged framebuffer-export boundary in packaging and design.
6. Benchmark each promising change against the unchanged portal baseline and
   previous game helper before promoting it. Remove unsuccessful experiments
   from the recommended/default path; preserve evidence and iterate on the
   measured bottleneck.
7. Integrate proven paths with existing backend settings, lifecycle and packaging.
   Keep user documentation short; record detailed research and raw artifact paths
   here. Run repository lint/test and affected native integration checks. Review
   NVIDIA/Intel compatibility separately from the available AMD hardware proof.

## Evidence required

Use the same owned animated source, visible source/output dimensions, encoder,
bitrate settings, receiver and measurement method. Include matching source/share
FPS, high source FPS, real GPU pressure and mixed CPU/GPU pressure. Include a
bounded resize/rate/pause/resume check. Use short screening runs first, then at
least three interleaved repetitions for a claimed win. Avoid an exhaustive test
matrix before a candidate shows useful improvement.

Report actual source FPS/frame times, unique decoded FPS, output dimensions,
codec/encoder, actual media bitrate versus bandwidth estimate, marker p50/p95/p99,
stalls, native CPU-readback fallbacks, process-tree CPU and per-process GPU engines.
Include compositor and game costs where observable. Separate startup from steady
state and capture overhead from the encoder/receiver shared by every method.
Synthetic high submission FPS does not establish a fix for Minecraft under load.
No account login, private-frame recording, or changes to existing applications
are needed for these local experiments.

A provisional meaningful-win gate is a repeatable reduction of at least 15% in
capture-attributable work, or at least 5 ms in p95 end-to-end marker latency, or
removal of reproducible capture stalls under matched contention. The win must
preserve delivered cadence, image dimensions/quality and source performance;
report absolute costs alongside percentages and measurement variability. A
capture-only win must not be described as an equal whole-system saving.

The goal remains open until the measured improvement and reliability checks are
established. Do not substitute theoretical copy counts, helper-only CPU numbers,
an idle voice channel, or software fallback for that evidence. If the current
native integration is the limiting factor, document and address that constraint
instead of claiming success from adding another backend name.

## Starting evidence

- [Existing pipeline map and measurements](capture-pipeline-20261102.md)
- [Game hook ownership and limitations](vkcapture-backend-20261102.md)
- [Native explicit synchronization](screenshare-sync-20261102.md)
- [Earlier capture research](capture-methods-20261102.md)
- [Pinned OBS vkcapture](https://github.com/nowrep/obs-vkcapture/tree/8a2c43295fae2ede8ffd382a1581281ceb6153c0)
- [Reviewed GSR source snapshot](https://dec05eba.com/snapshot/gpu-screen-recorder.git.r1536.099a55b.tar.gz)

The earlier portal/direct/game comparison did not benchmark GSR against OBS or
prove behavior under a saturated game workload. GSR's KMS capture and rendering
into encoder surfaces are distinct from the current ext-image-copy helper.
