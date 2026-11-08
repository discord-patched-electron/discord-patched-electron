# Capture efficiency implementation and results — 2026-11-02

## Delivered architecture

The [execution brief](capture-efficiency-plan-20261102.md) governs this pass.
The selectable game backend now prefers a direct DMA-BUF relay when the loaded
hook advertises support and synchronization/export preflight passes. Older hooks
retain the compatibility copy. This removes the helper's BGRA render/copy and
its GPU output pool; the helper passes descriptors and fences, not pixels.
It does not remove the hook's game-to-export copy or Discord's RGB-to-NV12/I420
conversion. PipeWire remains a buffer transport, not the compositor capture
producer, for this alternative.

The hook admits work at the requested capture rate, folds GL orientation into
its existing blit, and never waits for the viewer in the game's present path.
The helper forwards the hook's acquire fence into the native acquire timeline.
Only a successful consumer GPU release and successful original acquire fence
permit hook reuse. Old buffers retain allocation ownership across renegotiation.
Eventfd notification avoids a polling wait for release.

GL external-memory visibility is explicit. EGL cycles one imported semaphore
and exports its native completion fence with no Vulkan queue submissions.
GLX bootstraps once, then signals the next acquire and exported completion in
one Vulkan submission per captured frame. Strict UUID, format, modifier and
external-handle queries reject unsupported combinations. Vulkan DIRECT uses
SDR 8-bit explicit linear storage and a full-overwrite discard transition.
See [GL synchronization](vkcapture-gl-semaphore-20261102.md) and
[portability/fallback limits](capture-relay-portability-20261102.md).

Selected custom sources also use fresh-frame delivery: Discord's native thread
polls at 240 Hz, forwarding new frame identities and dropping repeats. Actual
producer capture remains at the requested rate. An opaque retained staging lease
prevents allocation reuse from impersonating an old frame; no pixels are mapped
or copied by this identity check. Static sources retain a 500 ms heartbeat.
CPU fallback preserves absolute requested cadence. Native portal delivery keeps
its existing default, with the same adjustment available for controlled tests.
[ABI and lifetime evidence](capture-delivery-20261102.md).

DPE Screensharing details report the selected GPU handoff and known fallback
reason. Readiness identifies the producer path, not proof that encoding has
started. Source selection remains in the existing DPE window; the native Discord
picker is outside this pass. Helpers and native adapters build independently of
Electron.

## Matched performance evidence

Artifacts are retained under
`/mnt/storage/dpe-capture-efficiency-20261102/`, with per-case input hashes,
configuration, decoded markers, CPU/GPU accounting, producer events and cleanup.
The native receiver is local; no account, SFU, saved video or physical camera is
involved. These results do not establish internet latency or a universal fix
for Minecraft on other machines.

The initial acceptance comparison alternated three portal and three direct-game
runs (`paired-fast-2000-*`). Each used the same owned 1080p source, 2,000 FPS cap,
1080p60 H.264 VAAPI sender, 6 Mbps setting, Electron receiver and GPU pressure.
Actual whole-run source submission was about 1,759–1,761 FPS; the cap is not an
achieved-FPS claim. Both methods received the same fresh-frame delivery change.

| Repetition | Portal p95 latency | Direct game p95 latency | Portal / game unique frames during pressure |
| ---------- | ------------------ | ----------------------- | ------------------------------------------- |
| 1          | 53 ms              | 45 ms                   | 409 / 421                                   |
| 2          | 53 ms              | 45 ms                   | 399 / 420                                   |
| 3          | 52 ms              | 45 ms                   | 421 / 420                                   |

The pressure interval was approximately seven seconds. These are source marker
to local decoded receiver measurements, not capture-only timestamps. Both paths
had zero duplicate decoded markers in these six runs, 1080p output, hardware
encoding and zero observed native rendition fallbacks. The gain meets the
brief's provisional repeated p95 gate while preserving cadence. Earlier
unadjusted relay runs did sometimes repeat frames; those failures are retained
rather than omitted from the investigation.

The helper recorded no GPU engine work and roughly 0.30–0.45% of one CPU core
in the game runs. That is **not** an equivalent whole-system saving: game-side
GPU work increased while compositor capture work fell, and native encode/decode
work remained. The demonstrated benefit is latency and removing a pixel copy,
not a large measured reduction in total system utilization.

The initial acceptance bundle preceded the final explicit GL semaphore cycle.
Final-cycle rechecks and their exact outcomes are recorded below; the source
fixture additionally becomes resizable for lifecycle tests without changing its
fullscreen rendering workload.

## Lifecycle and UI checks

`lifecycle-relay-09b` retains one native receiver session through 60→30→60 FPS,
1920×1080→946×1018→1920×1080 resizing, a 1.2-second owned-source pause, and recovery.
All checks pass: approximately 60/30/60 unique FPS in the corresponding running
phases, stable PipeWire node, restored dimensions, and clean producer teardown.
The first lifecycle run correctly failed its resize assertion because the SDL
fixture was not resizable; the fixture now permits resize and that failed case
is retained. Same-size graphics-context/texture recreation is still covered by
protocol/ownership fixtures, not this hardware run.

The scoped real Settings harness passes preferences, trusted diagnostics,
save rollback, keyboard and narrow/zoom behavior (`settings-handoff-03`). Its
light-theme screenshot was inspected. Two earlier full-UI attempts stopped at
an unrelated theme-editor size assertion under the host compositor; the affected
Screensharing harness runs on an isolated Xvfb display with a fresh profile.

## GSR KMS findings and implementation boundary

GSR KMS is worthwhile for a future monitor producer. It avoids compositor
screencast work and converts into hardware encoder surfaces in its own pipeline.
It is not general Wayland window capture: GSR uses its portal backend for that.
A KMS crop includes occluding windows and cannot replace vkcapture's application
semantics. NVIDIA's separate PID-specific Vulkan route is vendor-specific.

An exploratory GSR KMS/portal pair used the same owned fullscreen source and
hardware encoder, discarded encoded output to `/dev/null`, and retained resource
counters. KMS reduced observed compositor work in that pair. It has no decoded
receiver or latency markers, and only one paired repetition, so it is not a
Discord performance claim. See the [GSR runner and scope](capture-benchmark-workflow-20261102.md#gsr-kms-versus-portal).

The reviewed GSR KMS protocol exports framebuffer descriptors, plane geometry,
modifiers and related metadata, but no immutable-content lease or explicit
acquire/release fences. An FD retains storage, not unchanged scanout pixels.
A robust Discord producer should snapshot into owned GPU storage, combine crop,
rotation/cursor/plane composition in one pass, and release that storage only after
native GPU completion. Do not blindly relay scanout FDs or use a GSR encode→decode
bridge. Direct native encoder-surface injection has no verified stable C ABI here.
The existing privileged KMS helper was used for research; no new capabilities or
compositor configuration were installed. No KMS option is claimed implemented.

## Final build rechecks and handoff

The final explicit GL cycle (`final-gl-cycle-gpu-{1,2,3}`) retained 45 ms
constrained p95 in all three runs, with 420–421 unique frames and no decoded
repeats. A new portal control returned 53 ms and 405 unique frames. Thus the
portability synchronization change preserved the observed advantage; it did not
rely on the diagnostic semaphore bypass used in the first comparison.

`final-glx-60-02` verifies 1080p60 GLX delivery with the optimized one-submit
bridge; `final-vulkan-60` verifies 1080p Vulkan delivery through the guarded
linear export. `final-av1-720p30` verifies AMD hardware AV1 at 1280×720/30 FPS
under combined CPU/GPU pressure with a 4,000 FPS source cap. It delivers 210
unique frames in the seven-second pressure interval, with no decoded repeats.
That lower-resolution check is not included in the 1080p latency comparison.
The first final GLX fixture retained a smaller Xwayland window despite the
fullscreen request and correctly failed the resolution assertion; the fixed-size
owned GLX fixture passed the intended 1080p check. No encoder failure was hidden.

Repository validation passes: `bun run lint`, 644 Python tests and 339 JavaScript
tests. Native ownership, synchronization and delivery fixtures include ASan/UBSan.
The production source and hook artifacts match the tested final candidate.
The packaged picker metadata uses ABI 3 for its verified C exports.

A fresh candidate is at
`/mnt/storage/dpe-capture-efficiency-20261102/candidate/launch-test.sh`.
It reuses the verified Electron executable (SHA256
`c139aa38e2b0f60253d33fc592dd51ec967eca39d222a42c0d39b5dc5e68d1b9`),
contains independently rebuilt capture/helpers/UI, and uses a fresh profile
prepared from verified public module artifacts. No account data was copied and
no installed launcher or existing profile was changed. The pinned module and
all sidecars/scope copies are verified; full manifest details are in
`candidate/verification.json`. `packaged-pinned-smoke` also passes actual native
delivery with the packaged hooks and pinned module: 714 unique 1080p frames in
12 seconds, no decoded repeats, and all runtime validation gates passed.
The normal app was not logged in or joined to a
call during this pass. New hooks require relaunching the opted-in game.

The result is a measured improvement on the available AMD/niri machine, not a
claim of universal superiority, zero-copy encoding, a completed KMS backend, or
hardware verification on NVIDIA/Intel. The latter have explicit capability
checks and a [source/API review](capture-relay-portability-20261102.md), with
native-import/multi-GPU/separate-queue caveats preserved.
