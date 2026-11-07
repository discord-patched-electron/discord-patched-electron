# Capture preview repair and Rust migration — 6 November 2026

Native13 is built as `capture-latest`. All DPE native helper implementations
build from Rust; final live Vulkan/GLX/KMS validation, matched resource comparisons,
contention cases and fresh checks pass with the limits recorded in the
[final comparison](capture-native12-comparison-20261107.md). Pinned upstream
libraries, Electron patches and C test callers remain separate. The sections
below retain historical checkpoints and failed attempts; their earlier
in-progress statements do not describe the final qualification. Native13 repairs
SDR FP16 Vulkan export discovered after the Native12 benchmark series; the
measured GLX/KMS/runtime/encoder binaries remain byte-identical.

## 7 November: Rust encoder guard and final C implementation removal

`encoder.c` is replaced by `native/bridge/src/encoder_guard.rs` and
`encoder_install.rs`. Policy owns bounded handle/allocation maps, with no native
object ownership hidden in linked lists. Nested construction uses an owned,
bounded TLS stack; native calls and nested callbacks run outside policy/TLS
borrows. Native metadata and by-value encoder ABIs remain narrow unsafe boundaries.
AV1 level policy is shared with the private encoder through `dpe-core`; the unused
C implementation is removed. The encoder image is embedded with Rust
`include_bytes!` after the verified private codec build, replacing weak image
symbols and the assembly wrapper. The private namespace and source/license
correspondence are preserved.

Each hook group retains its loader and immutable original targets before redirect
publication. Reinstallation checks the complete scope and installed bytes. The
first native attempt rejected disjoint executable mappings while installing
allocation hooks. The common transaction now validates and patches only pages
containing actual calls, together with associated nonexecutable slots. Its
rollback retains callback owners if protection restoration fails. The failed
`rust-encoder-native-kms-av1-direct1` is retained.

`rust-encoder-native-kms-av1-direct2` passes actual AV1 1280×720 direct-GPU delivery:
716 unique decoded frames, 720 private GPU inputs, no CPU inputs, failures or
import fallbacks. Every encoder/picker/scale/sync installation verifies.
`rust-encoder-native-game-h264-pw1` passes Source-quality native H.264 lifecycle:
59.82→30.06→60.14 unique FPS, 60.18 after resize, 60.17 after fullscreen restore,
and 60.12 after workload recovery. The native quality correction runs 26 times;
the local stream-envelope policy runs twice. These are functional results, not
paired efficiency improvements. Rust tests cover native ABI arguments, nested
construction, level/suspension rules, metadata trust, allocation ownership,
retired/reused handles, lazy PLT installation and scope/byte tampering. Private
sysfs fixtures cover mixed and unknown AMD generations. Full fresh validation
and other decoder/backend combinations remain pending.

## 7 November: Rust game source and relay startup repair

`vkcapture-source.c` is replaced by the Rust game actor and typed GPU/hook owners.
A frame lease owns its texture generation and descriptors independently of native
PipeWire buffer retirement. Copy destinations retain GPU storage through fence
completion. Relay passes explicit acquire fences through without helper waits;
plain DPE frames require the hook's documented completed-work guarantee. OBS
compatibility remains copy-only. Relay avoids the previous unnecessary EGL texture
import; its resource effect has not yet been measured. Preallocation export
failure can change the hook allocation contract once; later cursor/rate changes
preserve the allocated contract.

Early relay prototypes stalled after one queue, while copy and the exact C
baseline passed. Queuing before triggering alone did not fix startup. A bounded
100 ms retry for an outstanding queued relay lease restores forward progress;
it never releases/overwrites the producer buffer. A counter exposes retries.
The native source retains non-ready leases across extra Process notifications.
Chunk publication validates every DMA-BUF plane before writing, without treating
the two trailing sync timeline records as image planes. Retired native metadata
is never touched by completion. Private loop/DRM fixtures exercise missing/failed
fences, import/transfer errors, completion after metadata retirement and teardown.

`rust-game-prototype6-relay1` passes: 714 frames published and released.
`rust-game-lifecycle-relay1` passes source resize/fullscreen, 60→30→60 and bounded
workload pause/recovery; `rust-game-lifecycle-copy1` passes the same transitions.
The coherent `rust-game-bundle1` contains 518 verified files. At this checkpoint
lint and 779 Python, 453 JavaScript (three skips) and 84 Rust tests pass; later
encoder changes require fresh checks.

`rust-game-pressure240-pw2` passes at 240 Hz with CPU and GPU pressure: 2,801
relay frames and releases, clean children, no invalid/out-of-order markers.
The marker sampler reports 813 initial, 456 constrained and 915 recovery unique
pictures; those counts alone are not a 240 unique-FPS proof. Initial/constrained/
recovery median source-to-receiver latency is 19/27/21 ms. The first pressure
attempt requested timer bypass at 240 FPS, where the adapter intentionally uses
normal native delivery; its delivery-mode assertion failed despite valid frames.
The corrected normal-delivery rerun passes. All failed attempts remain retained.
These runs qualify behavior, not a matched resource saving or zero-drop guarantee.

## 7 November: Rust Wayland producer qualification

The direct Wayland producer now uses the typed connection/leased-buffer owner
and the shared Rust PipeWire source. `wayland-source.c`, `wayland-capture.c`
and its C header are removed. The protocol builder now builds the Rust library;
`wayland-scanner` generates only the private fixture server protocols. Inventory
preserves monitor names, descriptions, physical dimensions, transforms and
logical output positions. Only the exact selected unrotated output is started.
GBM allocation opens its own device without initializing EGL. Preview keeps its
separate renderer. Dynamic dependencies of the Wayland source are PipeWire, GBM,
libgcc and libc; it has no EGL/GLES or encoder dependency.

One compositor frame owns its allocation until completion or dedicated socket
shutdown. Native buffer pointer retirement and GPU-storage retirement are separate.
The shared stream admission limit counts all retained allocations, including
retired buffers still held by a consumer, across renegotiation. Native data/chunk
initialization validates every plane before committing any field; malformed later
planes leave earlier records unchanged. The private wire fixture now also checks
compatible buffer reuse across generations and rejects retagging during a write.

The first native attempt rejected SPA's fixed `Choice None` representation.
The next attempts reached allocation but failed the link. The existing C baseline
passes with the same helper/runtime/content, isolating this as a port regression.
PipeWire tracks Format parameter serial changes during its `param_changed`
callback to acknowledge modifier fixation; deferring this update until after
native dispatch loses that handshake. The Rust fixation callback now computes
owned aligned PODs, releases every model borrow, then performs the synchronous
update. Other events remain queued. See the upstream
[stream implementation](https://github.com/PipeWire/pipewire/blob/master/src/pipewire/stream.c)
and [stream negotiation contract](https://docs.pipewire.org/page_streams.html).
The tested host PipeWire is 1.6.10. Core errors are observed with listener data
retained through partial construction, stream destruction and disconnect.

`rust-wayland-prototype5-pw2` passes native AV1 1280×720 delivery at 60 FPS:
712 unique decoded frames, 720 producer frames, zero skips and clean child exits.
`rust-wayland-lifecycle-pw1` passes native H.264 Source quality through measured
60→30→60 FPS and bounded workload pause/recovery, with fixed monitor dimensions.
It covers neither physical output reconfiguration nor a game context replacement.
The failed prototype attempts, missing-build invocation and C baseline are retained.
These are functional checks, not a matched CPU/RAM/GPU saving claim.

`rust-wayland-bundle1` contains 513 verified files. Repository lint and the full
779 Python, 453 JavaScript (three skips) and 80 Rust tests pass at this checkpoint.
GPU and private-server Rust cases are separately selected rather than opening
host devices in ordinary unit tests. The managed profiles remain Native11;
final rebuild/live delivery and matched extreme resource comparisons still wait
for the remaining encoder hook and game-source C ports.

## 7 November: Rust KMS source and monitor previews

`native/source` now builds the KMS PipeWire fallback and monitor preview
executables in Rust; `kms-source.c` and `screen-preview.c` are removed from
production builds. The optimized direct producer remains PipeWire free. The
KMS actor owns either an idle `Capture` or one `Pending` request, with a bounded
monotonic deadline. Stream callbacks enqueue owned events; the native
ALLOC_BUFFERS factory remains synchronous. Removed native buffers lose their
pointer immediately while GPU leases retain storage until release or teardown.
Node/DRM/Wayland calls run without Rust registry borrows spanning dispatch.

The shared Rust PipeWire source owner checks buffer identity, checkout/return,
event capacity and metadata bounds. Header writes initialize complete native
records without borrowing uninitialized storage. The owning DRM publisher pins
completion storage and loop watches independently of stream-buffer lifetime.
Source pacing has no periodic wake while the stream is inactive. This is an
architectural change; no paired resource saving is claimed yet.

The renderer retains primary/cursor texture names between frames, as GSR does,
while retaining per-frame EGL image import and completion fences. This removes
texture creation/deletion and sampler configuration from the KMS GL path. It
does not change the raw optimized producer, and has no measured saving yet.
The typed surface/fence GPU test passes with synthetic pixels on renderD128.

`rust-kms-prototype1-pw2` passes native AV1 720p60 delivery: 713 unique decoded
pictures in twelve seconds, 721 producer frames, no skips/failures and clean
cleanup. The earlier `pw1` launch rejected a wrong runtime pathname before
capture. The coherent `rust-kms-bundle1` contains 505 verified files.
`rust-kms-lifecycle-pw3` passes native H.264 at Source quality through measured
60→30→60 FPS and a bounded workload pause: 59.87, 29.67, 60.11 and 59.65 unique
FPS initially, at 30, after restoration and after recovery. Monitor geometry
stays fixed; this test does not cover physical output reconfiguration or consumer
pause. The first lifecycle invocation used the game-only harness incorrectly;
the second delivered every phase but failed its final command because the private
harness sent a broker command ID. Both failures are retained; the corrected
KMS harness sends the actual source command contract.

Rust monitor thumbnails retain their independent demand gate and watchdog.
KMS and Wayland each deliver ten changing 320×180 thumbnails; only hashes and
counts are saved. The executable links EGL/GLES/GBM/DRM, with no PipeWire or
encoder dependency in its dynamic inventory. The Wayland owner uses typed
protocols, a bounded event queue and an outstanding frame that owns its GPU
storage. Teardown shuts down its dedicated socket before releasing pending
storage. A real private libwayland server verifies selection, retries, batches,
generations, timestamps, damage, malformed events, hotplug, foreign-buffer
rejection and pending storage retirement. It uses synthetic SHM memfds only.
The older C adapter remains in the Wayland source until that producer is ported.

Repository lint and 779 Python tests pass at this checkpoint, followed by the
JavaScript and Rust suites. Later modifier-negotiation work needs fresh checks.
The full migration and final managed app rebuild remain outstanding. Four
project runtime C files remain: encoder, game source, Wayland source and its
legacy protocol adapter. The managed running version is still Native11.

## 7 November: Complete Rust picker owner and installer

`native/bridge/src/{capture_picker,picker_dispatch,picker_install}.rs` replace
the remaining `capture-picker.c`; the build no longer compiles that file.
Requests use a bounded Rust registry with explicit waiting, selected, starting,
active and cancelled states. Selected sockets remain owned until transfer to
the native constructor. Cancellation verifies descriptor identity and retains
a tombstone against delayed starts. Native callbacks, constructors and joins
run outside the request lock; only the reviewed nonreentrant update enqueue
and nonblocking TSFN enqueue use the documented serialization contracts.
Probe retirement follows the native join and occurs outside the request lock.

The dispatcher owns queued events and drains them on abort. Cleanup uses a
monotonic generation instead of a reusable TSFN pointer. Replacement and cleanup
share the core-to-dispatcher lock order, so an old environment cannot disable
its replacement. Active captures survive document cleanup until native destroy.
The installer reuses the shared bounded ELF/slot/call transactions and retains
loaded modules before publishing original callbacks. It checks named exports,
canonical lazy PLT entries, exact selected-storage layouts and all wake call
contexts before mutation. Reinstallation rejects changed scope offsets. Wake
delivery activates only after the entire redirect group commits successfully.

Five native fixture cases pass: request/descriptor ownership, callback delivery,
publication races, unknown scope rejection and real ELF/Node installation.
They cover descriptor reuse, full 32-owner admission, enqueue rejection, stale
cleanup after TSFN-pointer reuse, selected/ordinary callback lifetime, native
stop joins, invalid offsets with unchanged linkage, resolved/lazy slots and real
queued Node events. Disposable mock APIs are behind the fixture-only Cargo
feature; production loads the public Node API dynamically. Lint and the full
776 Python, 453 JavaScript (three skips) and 69 Rust tests pass at this checkpoint.

`profile-helper22` has SHA-256
`e9d97a370ed417317f9b67b3c62e66750ab43ab3285027929e26df7c71c706d4`.
`rust-picker-kms-pw1` passes KMS PipeWire fallback through actual native AV1
encoding and Electron decode: 712 unique 1280×720 pictures in twelve seconds,
718 valid timeline DMA-BUF captures, no producer failures and clean child exits.
It exercises request/cancel/request/started/stopped through the Rust dispatcher.
The later stress report also records `captureEventDelivery: true`, confirming
the optional wake redirects installed. This is functional evidence, not a new
resource comparison. Six project runtime C files remain, totalling 4,840 lines
before the following source-pause change; the full migration remains unfinished.

## 7 November: Producer admission while waiting for consumers

New Rust game hooks advertise a HELLO pause capability. Only advertising peers
receive zero-FPS CAPTURE controls; older hooks and vanilla OBS retain their
existing control semantics. Pausing retains the allocation and any outstanding
lease, accepts its exact acknowledgment and admits no new GPU copy. Resuming
keeps the allocation/generation and resets pacing without accumulated credit.
The direct producer uses this negotiated behavior too, replacing teardown during
temporary zero-FPS demand. Preview remains demand paced with its existing lease
gate. Native ownership fixtures and broker capability tests pass.

The PipeWire source bootstraps one texture for format advertisement, then pauses
the hook until a consumer is both streaming and negotiated. Format loss/paused
state pauses admission again; completion and exact lease retirement remain
unchanged. This removes work during setup and inactive consumer periods; it is
not a claim about steady direct-capture resource use.

Coherent `game-pause-bundle2` contains 489 files. With helper22 and the retained
managed runtime, `game-pause-pressure240-pw2` passes H.264 1080p240/24 Mbps with
twenty CPU workers and one GPU pressure worker during four constrained seconds.
Constrained native encode samples are 240, 240, 240 and 238 FPS. Its full
36.33-second source lifecycle admits/publishes 2,803 hook frames, of which 2,802
are relayed and acknowledged; the producer drops one frame. The owned workload
submits 8,717 draws at 239.922 FPS with no fence timeout. The sender still reaches
19 cumulative encoder-queue drops; this is not a zero-drop result. CPU marker
sampling remains insufficient to prove 240 unique receiver presentations per
second. The old-bundle replay below separates startup waste from active delivery.

The matched old replay, `game-pause-pressure240-old-pw1`, also passes with
helper22. It relays 2,806 frames, but publishes 8,328 and drops 5,522 across the
same setup/capture lifecycle. The new case relays nearly the same number while
avoiding that startup work. Both report wake redirects installed; this 240 FPS
control keeps the native callback cadence. No active-capture CPU saving follows
from these lifecycle publication counts alone.

Six dedicated ten-second tests (`game-pause-idle-{old,new}{1,2,3}`) measure only
the source waiting for a consumer, with no encoder/viewer connected. Order is
old/new, new/old, old/new. All pass with clean children. The same owned game
continues at approximately 240 FPS. Source plus helper CPU falls from 5.315%
to 3.872% of one core on average; paired absolute reductions are 1.238, 1.484
and 1.607 percentage points. Helper-only CPU averages 0.330% versus 0.082%.
One bootstrap capture replaces roughly 2,400 unused captures per case. Helper
RSS is effectively unchanged at about 86 MB; GPU-engine differences are small.
This qualifies waiting-state CPU savings, not steady screensharing or older GPUs.
The chart and parsed pairs are retained privately as
`game-pause-idle-20261107.html` and `game-pause-idle-comparison.json`.

The first direct stress replay used the deliberately encoder-free helper22 and
failed delivery: the direct GPU path requires the bundled private encoder for
H.264 too. That failed run remains retained. The frozen-helper wrapper now
rejects this combination before capture. Full `profile-helper23` has SHA-256
`c791b5110eb024a7aa75abbea18234b1f1c2c1dd4b789c5c9bdd96a36360d74e`.
With that helper, `game-pause-pressure240-direct2` passes: 2,573 conversions,
2,553 GPU encoder inputs, no CPU readback/failure and clean shutdown. Its
constrained encode samples are 173, 167, 161 and 151 FPS, so the direct-path
contention gap remains unresolved.

An unshipped two-submission encoder-depth experiment is retained as helper24.
At 1080p240, `game-async2-pressure240-direct1` passes with constrained samples
174, 173, 179 and 155 FPS. Inclusive encoder-send wall time falls from 4.404 s
to 0.460 s, while send-thread CPU changes only from 0.246 s to 0.229 s.
Constrained median marker latency increases from 33 to 36 ms. The source lease
still lasts about 4.5 ms on average, and VPP completion remains synchronous;
queueing has not removed that producer boundary. This single pair does not
justify a CPU/throughput claim or a deeper default queue. Source is restored
to depth one; do not package the experimental helper as the production result.

`rust-picker-events-kms-pw1` separately qualifies the complete helper23 with
actual fast/event-driven 720p60 AV1 delivery: 714 unique decoded pictures,
719 callbacks, all 719 fresh/forwarded, no capture drops and clean children.
The report records both installed redirects and active event-driven delivery.

## 7 November: Rust source negotiation

`native/pipewire/src/source_pods.rs` now owns the KMS and game sources' aligned
format and buffer offers. Geometry, FPS and plane counts are bounded; choices,
modifiers, mandatory metadata and sync-object blocks retain their contracts.
The native callback borrows immutable parameters only until its synchronous
connect/update returns. Reentrant negotiation owns a separate offer and cannot
overwrite the first call's bytes. Real SPA parsing/fixation checks both copy and
one-to-four-plane relay buffer contracts, header/timeline sizes, callback error
propagation, malformed input rejection and reentry. The remaining C dispatchers
use this Rust owner while their lifecycle migration continues. The new source
header and module are required in the bundled corresponding-source inventory.

## 7 November: Rust delivery leases and publication wakeups

`native/bridge/src/capture_delivery.rs`, shared `capture_abi.rs` and the pure
`native/media-core/src/delivery.rs` replace the picker bridge's C delivery
implementation. At this earlier delivery-only checkpoint, request/installation
code still called the Rust owner through a small declared ABI: `capture-picker.c`
fell from 1,238 to 898 lines and seven runtime C files remained. The complete
picker port above later removed that file.

Opaque generation tokens own probes. Thread-local bindings retain weak owners;
a sleeping receive pins its probe through wake/retirement without dereferencing
freed C state. Publication matches bounded weak registrations by framebuffer
identity and avoids the picker request mutex. Native stop/config messages win
both polls around a frame wait, bursts coalesce and a 500 ms deadline supplies
heartbeats. The native receiver keeps its own Result storage; Rust reads only
the reviewed timeout discriminator and does not interpret its return-register
payload or construct a foreign Rust object.

The callback acquires a new native staging lease before retiring the previous
lease, preserving ABA protection without reading pixels. Old/unclaimed leases
drop outside model locks. Output records are written through raw output pointers
without borrowing uninitialized C storage. Failed native updates retain previous
callbacks and cadence. The reviewed native update only enqueues options and
cannot invoke a callback inline; its configuration lock preserves the existing
atomic callback/update behavior. Diagnostic write failures cannot panic across
the C boundary. CPU fallback retains phase under jitter instead of turning a
requested 60 FPS into 48 FPS; missed slots expire without frame credit.

Actual Rust/C ABI fixtures pass FD transfer, selected/ordinary capture lifetime,
lease identity, duplicate suppression, failed updates, 30→60 changes, unknown
frame cadence, publication before/during waits, native-message priority, burst
coalescing, heartbeat and joined stop. Their native mock frame destructors remain
ASan/UBSan-instrumented. `profile-helper21` has SHA-256
`cc20ad64601804ba47ac7f87af4be702070d4a67d3664a99d4c5c442ca440891`.
`rust-delivery-kms-pw1` passes actual KMS PipeWire fallback with 692 unique AV1
720p60 pictures, no producer failure/buffer-return error and clean shutdown.
The full repository suite ran alongside this functional check; its resource
counters are not a performance comparison. Repository lint and all 775 Python,
453 JavaScript (three skips) and 69 Rust tests pass. The Native11 account
sessions have not yet been rebuilt with this bridge.

## 7 November: Standalone Rust game preview

`native/graphics/src/bin/game-preview.rs` and its safe thumbnail owner replace
the 250-line `vkcapture-preview.c`. The executable has no PipeWire library
dependency and creates no PipeWire loop, cursor connection or encoder. It
validates the inherited socket type and same-user peer before GPU setup. DPE
seqpackets remain exact-sized; OBS stream fragments use the shared ancillary
receiver, which adopts every FD immediately and drops all owners on failure.
The existing exact-packet API still rejects short/truncated packets. OBS's
packed layout is decoded explicitly with bounded dimensions/strides/offsets.

The helper retains producer UUID selection, the bounded watchdog, persistent
demand pacing and the 25 ms initial OBS head start. DPE release follows completed
GPU thumbnail readback and carries the exact generation/sequence. Base64 output
uses a fixed 4 KiB scratch buffer and batched writes. It adds no image-sized
encoding buffer, full-source readback or per-frame descriptor-vector allocation.
The source builder no longer generates unused C Wayland protocol tables for
these consumers. The game source still has its explicit PipeWire fallback;
optimized direct transport remains separate.

`rust-game-preview-bundle1` is a coherent 469-file bundle. Its preview SHA-256 is
`e3d6a26925d4272b0b9a37b21e13ed3dc49c095b72e6554a9ab2e415b56d76b7`.
`readelf` lists EGL, GLES, GBM, libgcc and libc, with no PipeWire dependency.
`rust-game-preview3` passes ten changing persistent 320×180 thumbnails from a
private owned game/broker; no pixels are saved. Real stream-fragment/SCM_RIGHTS
tests, OBS validation, bounded base64 and argument checks pass, alongside full
repository lint and all 775 Python, 453 JavaScript (three skips) and 67 Rust
tests. The optional synthetic GPU cursor test is separately qualified above.
The supplied source rebuild recipe compiles the consumers without the original
checkout; its earlier C-preview version was exercised in
`game-corresponding-rebuild1`; its Rust-preview replay also passes in
`game-corresponding-rebuild2`.

Seven project runtime C files remain, totalling 6,078 lines, excluding headers
and vendor code. The current managed sessions still use Native11. The migration
and final matched resource comparison remain incomplete.

`rust-game-gpu-copy1` separately qualifies the actual Rust GL copy fallback,
forced with `--game-handoff copy`: 713 unique AV1 720p60 pictures, 720 producer
frames, no producer failure and clean shutdown. Its 719 captured DMA-BUFs have
no sync-timeline metadata, as expected: the owned native fence completes before
the copy is queued to PipeWire. Relay qualification above does not exercise
this shader/copy path.

The clean `rust-game-gpu-pressure240-direct2` stress case passes without PipeWire
and closes cleanly, but constrained native encode samples are 143, 173, 162 and
170 FPS. It has no capture failure or full-resolution readback. Source telemetry
still records 240.001 submitted FPS; 292 `heldPresents` are skipped capture
admissions while an image lease remains outstanding, not blocked game presents.
This result warrants more matched investigation before a throughput claim.
Aggregate direct stages record roughly 1.05 s waiting for acquire fences and
1.22 s in VPP completion across 2,593 received frames; the maximum VPP wait is
5.1 ms. Encoder send spans 4.36 s wall but 0.254 s thread CPU across 2,546 calls.
These inclusive totals cover the whole capture and do not locate individual
constrained-phase stalls. Do not sum nested timings as independent costs.

The relay helper's lifecycle counters cover roughly 36 seconds, including
capture before the twelve-second receiver interval: 8,321 hook publications
and 5,540 producer drops therefore do not describe active delivery alone.
The direct helper captures only during its connected consumer interval. This
also exposes unnecessary relay work before consumer demand, which should be
addressed during the source-helper rewrite and measured separately from steady
capture. Final resource comparisons must retain equal delivered cadence and
separate idle, startup, steady, contention and recovery intervals.

## 7 November: Rust game GPU copy and preview owners

`native/graphics/src/{game,game_render}.rs` replace the 490-line
`vkcapture-gpu.c`. GPU, image, texture, framebuffer, GBM buffer and export owners
retain their context explicitly. Retiring a public GPU handle cancels its
pending source while outstanding buffers keep their storage alive. Thread-local
bounded tokens reject cross-thread/stale handles. Each completion has a new
generation; cancellation removes its registration before releasing resources.
No Rust borrow spans a consumer callback. Registration handles synchronous
completion or destruction without publishing a stale source, closing its FD
early or acknowledging a second time. After bounded table warm-up, completion
reuses existing owners/table capacity without a per-frame Rust heap object.

The path retains the original GPU triangle copy, exported native fence and
event-driven completion. A thumbnail uses its own small GL texture/FBO, scales
before readback and retains the 480×270 cap. Cursor storage now has a shared
bounded Rust parser. Cursor shape uploads track serial and dimensions; borrowed
pixels remain inside the Rust cursor owner's callback. Cursor connections are
created only for an actual copy, so picker preview does not initialize them.
KMS builds exclude the optional game/cursor feature. CI lint and Rust tests
explicitly include the game feature. Combined game consumer binaries now ship
the GPL-3.0 notice and complete Rust graphics source; MIT source notices remain.

`game-graphics-ownership1` passes on `renderD128` with owned cleared buffers:
channel order, real fences, repeated completion, failed I/O registration,
synchronous completion/retirement, stale callbacks, cross-thread rejection and
buffer lifetime after GPU retirement. No desktop content is used. The old
private-C cursor fixture moved to an explicitly selected Rust GPU test; it
passes exact red/green sprite placement, premultiplied alpha, negative hotspot
clipping and hidden state. These pixels remain transient memory only.

`rust-game-gpu-bundle2` is a coherent 480-file bundle. With `profile-helper19`
and the retained managed runtime, `rust-game-gpu-pw1` passes actual game
PipeWire fallback: 715 unique AV1 720p60 pictures in roughly twelve seconds,
31 ms median marker latency, 720 valid explicit-sync frames, no producer
failure and clean children. `rust-game-preview1` passes ten changing persistent
320×180 previews from a separately owned game and private broker. Only metadata
and hashes are saved. This bundle predates the final lazy cursor initialization;
that small follow-up still needs a coherent rebuild and runtime check.
Repository lint and all 775 Python, 453 JavaScript (three skips) and 63 Rust
tests pass before that follow-up; the GPU-only cursor test is separately passed.
Eight project runtime C files remain, totalling 6,328 lines, excluding headers
and vendor code. The managed Native11 sessions have not yet been rebuilt with
these ports. Matched final resource and contention comparisons remain pending.

The lazy-cursor follow-up passes ten changing persistent previews in
`rust-game-preview2` from coherent `rust-game-gpu-bundle3`; lint and the full
suite pass again. `rust-game-gpu-bundle4` also supplies the standalone source
rebuild recipe and enables section garbage collection for the consumer link.
This reduces executable file sizes; no resident-memory saving is inferred.
`rust-game-gpu-pressure240-pw1` uses bundle4 and helper19 at H.264 1080p240,
24 Mbps with a 240 FPS owned source, twenty CPU workers and one GPU load during
the four-second constrained phase. It passes with clean children and no producer
failure. The four constrained native encode-rate samples are 234, 239, 240 and
229 FPS; the sender records 31 cumulative encoder-queue drops by the end of the
case. This is not a zero-drop result. CPU marker readback yields 1,199 unique
pictures and cannot qualify 240 unique presentations per second. A separate
direct-path case is retained for follow-up; it overlapped source-rebuild work
and is unsuitable for a resource comparison.

## 7 November: Rust PipeWire negotiation owner

`native/bridge/src/capture_upstream.rs` and `native/pipewire/src/{pod,rates,upstream}.rs`
replace the 749-line `capture-upstream.c`. The Rust POD parser retains aligned,
immutable offers and preserves every unrelated property, flag and modifier byte.
The default adds an initial DROP preference with the original broad fallback
range. It creates no timer and performs no later renegotiation. The optional
runtime mode keeps the existing 100 ms owner-loop timer, requires an actual
format acknowledgment, bounds acknowledgment waits to two seconds, and restores
original offers once on failure. It leaves native ERROR recovery in control.

Owned publications remain live across callbacks. Revision tickets prevent an
older update from overwriting nested replacement caps. Bounded generation tokens
retire hooks/timers before dropping state, reject stale callbacks and avoid
native calls while registry/model locks are held. Timer creation commits only
to a live owner; destruction during listener registration prevents a subsequent
connect to the retired stream. Callback thread checks preserve the existing
owner-loop restriction. Foreign destruction requires native serialization with
the loop stopped or its context held, as required by SPA source removal.

Installation still checks the reviewed FF15 call, its GOT slot, the complete
14-byte original stub and its writable inner function word. It now redirects
only the two reviewed call sites, leaving other PipeWire callers and GOT slots
unchanged. A real loaded ELF/Node fixture verifies both modes, unchanged unrelated
calls, idempotence, invalid offsets/targets, no partial installation and rate
validation. Separate C callbacks exercise the actual Rust adapter's nested
updates, acknowledgments, timer failures, destruction, stale tokens, foreign
thread guards, reconnects and the full 16-stream registry. Real SPA intersection
and fixation tests cover the reviewed 25-default/0–1000 offer against fixed,
variable and absent producer rates. Pure Rust state tests cover timeouts and
late acknowledgments without wall-clock sleeps. These fixtures capture no pixels.

`profile-helper19` has SHA-256
`477f6c595b24ba982316b2f3dc70cd23cee500ab3efa3161c048c3d3045148d5`.
`rust-upstream-kms-pw1` passes actual KMS PipeWire fallback with the retained
managed runtime: 709 unique AV1 720p60 pictures in roughly twelve seconds,
45 ms median marker latency, 719 DMA-BUF frames with valid explicit-sync metadata,
zero capture/negotiation errors and clean child shutdown. It records one initial
preference and no runtime renegotiation. This is functional qualification;
repository verification ran alongside it, so resource counters are unsuitable
for a performance claim. The running Native11 dev/clock sessions still use
the earlier verified helper. Nine project runtime C files remain, totalling
6,818 lines, excluding headers and vendor code.

Repository lint and all 774 Python, 453 JavaScript (three skips) and 62 Rust
tests pass at this checkpoint. The first full run exposed an unnecessary DRM
relay dependency in standalone Node fixtures. A Cargo feature now excludes
that relay from the Node adapter; the actual PipeWire producer retains it.

## 7 November: Rust native PipeWire capture adapter

`native/bridge/src/capture.rs` replaces the 475-line `capture.c`; queue and
metadata regressions now live in `capture_tests.rs`. Native buffers, chunk
inventories and metadata stay borrowed for one callback. Fixed-capacity batches
preserve bounded newest-frame selection and reverse FIFO restoration.
Cursor/control, unknown metadata and explicit-sync frames retain native order;
failed restoration reports a stream error without releasing an unconsumed
fence. The existing Rust pacer resets by stream/configuration generation and
retains unlimited mode, jitter tolerance and no accumulated frame credits.
There is no per-frame heap allocation or new GPU operation.

The reviewed native dequeue returns a Rust `Option<Buffer>` through two x86-64
registers. The empty niche initializes RAX but need not initialize RDX; the
adapter stores that payload as `MaybeUninit` and never reads it on an empty
result. Shared patch installation now validates complete FF15 calls and their
readable target slots before creating typed original callbacks. Logical ELF
text ends can fall within loader-owned page padding, so page permission checks
also require the actual complete RX mapping. Both indirect calls are validated
before either changes. Protection failures reestablish write access before
rollback and retain code/library owners if restoration cannot be established.

The real loaded ELF/Node fixture passes observe, latest and paced modes. It
checks the native two-register ABI, rejects wrong targets/call bytes, overlapping
calls and out-of-range offsets without installing, and verifies idempotence.
The old C-only queue fixture was replaced by Rust tests of bounded ownership,
FIFO recovery, malformed metadata, sync observation, failed return, pacing and
clock resets. A disposable Cargo feature replaces the C callback-delay helper.
It shares the real capture implementation, tests bounded delay through a real
native call, and is absent from product exports. Its linker export map excludes
unrelated native entry points. Default and fixture Clippy checks pass.

`profile-helper18` has SHA-256
`41f732aac0fbea5de12fa2a8c7ff62dc409e4bdb248b4c16d1cc511267f8a99f`.
`rust-capture-kms-pw1` uses that helper and the retained managed runtime with
actual KMS PipeWire fallback. It delivers 707 unique AV1 720p60 pictures in
roughly twelve seconds and closes its children cleanly. All 719 observed DMA-BUF
frames retain valid explicit-sync metadata, with no invalid timeline, skipped
control frame or buffer-return error. Compilers/tests ran alongside this
functional qualification; its resource counters are not a performance comparison.
`rust-capture-game-direct1` also passes the optimized native Wayland game path
with the same helper: 717 unique AV1 720p60 pictures, no PipeWire streaming,
zero capture failures/full-resolution CPU readbacks and clean shutdown.
Repository lint and all 770 Python, 453 JavaScript (three skips) and 59 Rust
tests pass. Capture fingerprints and benchmark source inventories now include
the Rust inputs. Ten project-owned runtime C files remain, totalling 7,567
lines, excluding headers and vendor code. These latest adapter changes are
not yet in the running Native11 managed sessions.

## 7 November: Native11 managed checkpoint

`capture-rust-native11`, refreshed `capture-latest` and independent
`capture-rust-clock4` have byte-identical helpers, capture bundles and runtime.
The 458-file capture manifest SHA-256 is
`38a54eb6dcbc2e5ce187bf747f31214bc5d641a166cca8b139480df14af77c89`;
the direct producer is
`ded2756305a686a20fdd91b6ba756b59cc45e78937034655d6bf8c0237584dc5`.
The native Node helper/private encoder remain byte-identical to Native10;
the changes are in the standalone producers, fallback GPU helper and opted-in
hooks. The runtime and installed privileged helper also remain unchanged.

Native11 passes fresh-profile launch/relaunch in `native11-desktop/` using
`capture-rust-verify11`. A redundant explicit-Welcome check in
`native11b-desktop/` failed on the manager's catalog lock during concurrent
rebuilding, after correctly gating the client and completing Welcome. Its
report/log are retained; this was an orchestration conflict, not a capture or
module-API failure. Subsequent account managers were started sequentially.
The existing dev and clock profiles were retained; both were verified idle
before their old managers were stopped. The previous generated latest build
was copied with filesystem reflinks for rollback; no account profile was copied.

Repository lint and all 770 Python, 453 JavaScript (three skips) and 57 Rust
tests pass after the cancellation/verdict changes. The live game share from
dev on refreshed latest passes with receiver playback and advancing 320×180
sender preview, without 2011. The owned fixture loads the new GLX Rust
synchronization hook. Live KMS shares subsequently pass in both directions: dev delivers 607
receiver pictures over 10.15 seconds, and clock delivers 384 over 12.83 seconds.
The dev KMS preview advances from 7 to 99 pictures; its cumulative native
preview count reaches 225 without an error. Game preview had advanced from
21 to 114 pictures, with 734 receiver pictures over 12.23 seconds. All three
traces report direct GPU transport, zero full-resolution CPU readbacks and no
capture failures. Clock's KMS share requests no local preview. Both accounts
are disconnected afterward, and no verification dialog is present.

## 7 November: consumer-reset teardown and benchmark verdict

The first final-hook 1080p240 contention run,
`rust-gl-sync-pressure240-game1`, delivers video and recovers after twenty CPU
workers plus a GPU worker. Its native receiver reports a delivery pass, but
the producer subsequently reports `Connection reset by peer`, `failed: true`
and exit 1. This run does not qualify clean capture teardown. The old benchmark
verdict checked receiver delivery and top-level child cleanup while overlooking
the nested producer's final failure event; the evidence remains retained.

The direct producer distinguished orderly EOF from malformed control messages
but treated a consumer closing with an unread frame as a capture failure.
Linux can report that close as a socket reset. Consumer receive and frame-send
boundaries now recognize only disconnected-consumer errors as cancellation.
Hook/DRM/fence errors and malformed packets still fail. Cancellation does not
acknowledge or recycle an outstanding hook lease. Two real socketpair regressions
cover unread-frame reset and disconnect during frame forwarding; malformed hook
messages remain errors after consumer loss. All seven producer Rust tests pass.

The benchmark now inspects final producer events after draining cleanup output.
A producer failure/error code or positive stopped exit overrides a delivery pass,
is retained in `result.json`, and makes the command fail. Its new regression
reproduces the previously misleading combination; all fifteen benchmark tests
pass. The coherent `rust-gl-sync-bundle3` contains the corrected producer and
final synchronization guard. `rust-gl-sync-pressure240-game2` passes the repeated 1080p240 H.264/24 Mbps
check with twenty CPU workers and one GPU worker, recovers to approximately
239 native encoded FPS, and now reports `failed: false`, an empty error code,
producer exit 0 and no final producer failures. Constrained native encode
samples average 171.25 FPS. The CPU marker receiver observes fewer unique
pictures and does not independently prove 240 unique pictures per second.
This single recovery case is not a matched performance-improvement claim.

## 7 November: Rust GL/Vulkan synchronization

`native/game/src/gl_sync.rs` replaces the 423-line project-owned C semaphore
helper. Header-derived Vulkan/GL function tables remain dynamically resolved;
no Vulkan or GL loader is added to the direct KMS executable. Explicit cycle
state and scoped FD transfers preserve the GLX GENERAL-layout release and
native EGL-fence paths. A bounded vector replaces the retired linked list.
A process-wide mutex serializes host access to native sync objects; current
context checks guard GL operations. Recursive driver callbacks fail closed
without deadlocking or corrupting an active cycle. Context-destruction notices
apply across threads, and reused context pointers cannot delete unrelated GL
names. No per-frame Rust heap allocation or GPU wait was introduced.

Review found an existing failure-path bug: after a successful GL wait/copy/signal,
a failed final Vulkan submission left no fence covering that GL work. The C
helper could reclaim the semaphores after only the earlier Vulkan fence had
completed. Rust now retains this untracked work until context teardown instead
of treating the earlier fence as sufficient. The new driver-independent ABI
regression fails against the retained Native10 C implementation and passes
against Rust. The fixture links the real archive and checks driver-visible live
objects rather than including private implementation globals. It also covers
reentrant submission, export/import failures, bounded retirement, device errors
and context replacement. A legacy missing-FD completion result is accepted
only after the actual submission fences independently prove completion;
negative export results alone never permit reuse. The Vulkan
[export ownership contract](https://docs.vulkan.org/refpages/latest/refpages/source/vkGetSemaphoreFdKHR.html)
requires separately releasing each exported descriptor.

All twenty backend tests and Clippy pass. `rust-gl-sync-bundle1` contains 458
files and complete corresponding source; its real GLX and native Wayland/EGL
AV1 720p60 checks deliver 713 and 717 unique pictures over roughly twelve
seconds, with zero capture failures or full-resolution CPU readbacks. Their
19 ms median source-to-CPU-receiver latency is a delivery diagnostic, not a
matched resource comparison. The final missing-FD guard was added afterward
and needs a refreshed bundle. Eleven project-owned runtime C files remain,
containing 8,042 lines, excluding headers and vendor code. The all-native
migration and final matched performance qualification remain incomplete.

## 7 November: Rust KMS graphics ownership

`native/graphics/` replaces the 332-line KMS GPU rendering implementation.
The separate GPL-3.0-only crate links into monitor thumbnails and the KMS
PipeWire fallback; the optimized direct producer remains independent of EGL.
Its context owns the DRM file, GBM device and EGL lifetime. Scoped owners retire
framebuffers/textures, EGL imports, exports and backing allocations in order.
A buffer retains its context and shader program after the public GPU handle is
retired. Thread-local bounded tables reject stale and foreign-thread handles
without dereferencing freed C allocations. EGL extension tokens and up to four
DMA-BUF plane attribute records are validated before import. Native fence export
and the original GPU composition/readback boundaries remain intact.

Rust tests and Clippy pass. The real GBM/EGL ABI fixture on renderD128 verifies
cross-thread rejection, device-before-buffer retirement, borrowed packet FD
lifetime and repeated destruction. Eight injected framebuffer-completeness
failures leave the open descriptor count unchanged. The fixture creates only
owned blank buffers and never captures or saves a desktop image.
`rust-graphics-preview-check1` runs the retained C checkpoint and new Rust helper
against the same owned fullscreen shader. KMS and direct Wayland each return
ten changing 320×180 thumbnails and exit cleanly; pixels stay in memory and are
discarded. The coherent 452-file `rust-graphics-bundle1` includes full
corresponding Rust sources. The full repository check passes: 769 Python, 453 JavaScript (three skips)
and 55 Rust tests. `rust-graphics-kms-pw1` then passes actual PipeWire fallback
streaming with 712 unique AV1 720p60 pictures and clean child shutdown. No
resource saving is claimed from the language change.
Twelve project-owned runtime C files remain, totalling 8,465 lines, excluding
header implementations and vendor code. The current managed Native10 account
sessions still use the earlier graphics helper until another rebuild.

## 7 November: supervisor frame-rate protocol repair

Managed Native9 passes fresh-profile launch/relaunch and current-runtime KMS
and native Wayland game loopback. Both GPU cases deliver 717 unique AV1 720p60
pictures with zero capture failures or full-resolution CPU readbacks. Its
435-file capture bundle and native helper were installed as `capture-latest`;
the dev profile and a separately built Clock2 were launched through the manager.
An approved live KMS share from dev delivers 707 pictures to the receiver and
advances the sender's bounded preview without 2011. The retained runtime still
decodes the known nonaligned AV1 case as 1920×1082.

The reverse Clock2 test exposed another startup bug. Its source process sends
one frame, then exits with an invalid-command error before the native worker
can acknowledge that frame. The main-process supervisor serializes rate changes
as JSONL (`{"op":"rate","fps":30}`); the direct producer accepted only the
standalone `rate 30` syntax. The separate PipeWire KMS/Wayland helpers already
accept JSONL. A rate change during startup therefore stopped only the direct
producer. This also explains why an earlier Clock1 retry could succeed after
its requested rate had already been cached. The failed Native9 reverse trace is
retained; both profiles were disconnected after the test.

`native/media-core/src/control.rs` now parses the existing supervisor record
and standalone form without allocation or unsafe code. Inputs are bounded to
63 bytes and 0–240 FPS; JSON accepts either field order while rejecting duplicate
or unknown keys, noninteger numbers, overflow, malformed whitespace and trailing
data. The real producer's buffered-command unit uses the supervisor's JSONL
between text commands, reproducing the missing contract before the fix. Errors
now identify an invalid rate command without logging its contents. Repository
lint and all 768 Python, 453 JavaScript (three skips) and 53 Rust tests pass.
Native10 passes fresh-profile launch/relaunch. `native10-json-rate1` uses its
actual managed runtime/helper/producer with a hardware AV1 720p60 receiver, then
writes the supervisor's JSONL rate changes to the real source's stdin. Native
encoder samples change from 60 to 31/30 and recover to 60 FPS; delivery and marker
checks pass with zero capture failures. This specifically crosses the command
boundary missed by earlier steady-state and broker-driven lifecycle checks.
The repaired Native10 versions subsequently pass approved live KMS shares in
both directions and game sharing from clock to dev. Reverse KMS delivers 289
receiver pictures over 9.62 seconds; game delivers 287 over 9.57 seconds. Both
negotiate approximately 30 FPS at 1024×560 and advance the sender preview.
Dev-to-clock KMS delivers 736 receiver pictures over 12.32 seconds at
1920×1082, without 2011. That dev trace requests no sender preview, so it does
not qualify local preview playback. All three native traces use direct GPU
transport, report no capture failures or full-resolution CPU readbacks, and
observe no PipeWire dequeue callbacks. Counters in clock's second trace are
cumulative across both successful shares. Both accounts were disconnected.

The private call harness now locates the current isolated context and recognizes
React-provided confirmation-button labels. Failed attempts are retained:
Native10 reverse attempts 2/3 time out during picker/confirmation automation
without constructing a direct capture or confirming Go Live; attempt 4 passes.
A read-only renderer stack attachment was denied by the host ptrace policy; no
policy was changed and no stack was captured. The first game attempt looked for
a “Window” source label where Discord exposes “Application”; correcting the
private fixture makes selection and streaming pass. These automation failures
are not evidence of a native capture or renderer deadlock. No verification
dialog was present during the successful cases.

## 7 November: Rust direct producer and fallback relay

`native/producer/` replaces the 657-line `direct-source.c`. Cargo builds the
standalone GPL-3.0-only executable. Its KMS interface owns validated replies and
SCM_RIGHTS descriptors without transferring raw FD ownership through C records.
Frames can outlive their sessions. Fixed-capacity borrowed descriptor lists and
an explicit 296-byte codec avoid per-frame heap allocation and C/Rust padding.
KMS requests retain one request across interrupted waits, use a monotonic
deadline and check cancellation at bounded intervals. Read-only plane/layout
validation remains mandatory. Exported and merged acquire fences remain intact.

The single consumer owns one outstanding frame lease. Generation/sequence
checks gate release, and pause/rate changes preserve that lease. Failed exports,
imports and sends unwind their descriptors. An unstarted selection may be
cancelled and replaced; malformed messages are distinct from socket EOF.
Scoped cursor borrows cannot escape their owner. Only a changed cursor shape
writes a sealed bitmap; position updates reuse its descriptor. Cursor owners
cannot move between Rust threads. Vulkan loads only for game UUID inventory;
the KMS producer no longer links or maps the Vulkan loader. KMS pacing retains
a fixed phase, skips missed credits and uses nanosecond `ppoll` deadlines rather
than rounding each wait up to a whole millisecond. This does not increase the
requested capture rate or prove a throughput gain.

`rust-producer-bundle1` passes managed-runtime native GPU loopback for KMS,
native Wayland games and X11 games. The three roughly 12-second AV1 720p60 runs
deliver 718, 717 and 717 unique pictures, with median source-to-CPU-receiver
latencies of 22, 18 and 18 ms. Capture reports zero full-resolution CPU
readbacks. `rust-producer-kms1` was an invalid CLI attempt (`720` instead of
`1280x720`); it never started capture and remains retained. Bundle2 includes
final cursor thread confinement and closes the consumer before source teardown.
Its four observer-comparison KMS runs below also pass delivery. These checks
do not qualify a live call on the new producer or establish a resource saving
against the previous C producer.

`native/pipewire/src/relay.rs` replaces the separate 211-line fallback relay.
This crate is absent from the optimized direct executable. It owns DRM sync
objects, notification/source registrations, exported timelines and imported
planes. Thread-confined, bounded handles replace freed allocation pointers.
Registry/state borrows end before C completion callbacks; a callback may destroy
its own relay. The loop registration is revoked before descriptors close.
Completion never reads retired PipeWire metadata. A received acquire fence is
owned even when a stale handle or failed submission rejects it. The relay keeps
its event-driven release notification and requires both successful acquire and
the consumer's GPU release point before acknowledging reuse. No polling timer,
CPU pixel mapping or weaker synchronization was introduced.

The existing C producer/relay fixture now links the real Rust archive. GNU
linker wrappers supply synthetic DRM operations, while the fixture supplies an
actual SPA loop-utils method table. It covers early PipeWire buffer return,
metadata retirement, stale generation, forwarded unsignaled acquire, errored
fences, import/transfer failure, callback-driven relay destruction and shutdown.
The reconfiguration fixture also links Rust. All 20 backend tests pass.
`rust-relay-game-pw1` and `rust-relay-kms-pw1` subsequently pass real GPU
fallback loopback, explicitly observing PipeWire streaming: 711/712 unique
AV1 720p60 pictures over roughly 12 seconds. Game relay delivers and releases
717 leases without an error. These checks qualify fallback behavior, not a
performance improvement or equivalence to optimized direct transport.
Thirteen project-owned runtime
C/C++ files still contain 8,797 lines, excluding header implementations and
vendor code. The all-native migration remains incomplete.

## 7 November: benchmark observer cost

The benchmark now records its sampling thread CPU/wall time and whole observer
process CPU separately from capture totals. Child-tree caches last only one
sampling pass, preserving children forked from nonleader threads and fresh
discovery on the next pass. FD counters reject non-DRM devices before parsing
their `fdinfo`; per-client engine/memory deduplication is unchanged. The sampling
thread lowers only itself to nice +10, preserving a lower inherited priority.
The first combined attempt did not reduce measured CPU; those results are kept
as `observer-baseline1` and `observer-optimized1`.

A controlled microbenchmark pins only its own observer process to one allowed
CPU. Against the same 29 app/compositor processes, proportional-memory reads
cost about 30 ms per pass, compared with about 10 ms for device-filtered counters
and about 13 ms for parsing every FD. Both counter paths find the same nine DRM
clients. These are diagnostic microbenchmarks, not application savings.

PSS now defaults to one actual snapshot every two seconds and at phase changes.
CPU, RSS and DRM counters retain their 0.5-second sampling interval. Unmeasured
PSS stays absent, is excluded from memory means and is never carried forward.
The summary reports its snapshot count. The CLI retains a 0.5-second PSS option
for transient-memory investigations.

`observer-memory-{fast,sparse}{1,2}` alternates the two PSS cadences with the
same Rust bundle2, `profile-helper16`, managed runtime, 1080p240 source, hardware
AV1 720p60/6 Mbps, ten-second idle baseline and 30-second capture. All four pass,
with 1,796–1,799 unique pictures and 23–24 ms median latency. Mean sampling CPU
is 5.626 versus 3.360 percent of one core during capture and 4.903 versus 2.828
during the idle call. Each capture has 57–59 ordinary samples; the sparse mode
takes 15 actual memory snapshots. This reduces observer interference; it is
not a screenshare pipeline CPU saving. No compiler, test suite or separate
pressure load ran alongside these measurements. Raw evidence is
`observer-memory-summary.json`; the interactive artifact is
`out/capture-observer-overhead-20261107.html`.

Before the relay port, repository lint and all 768 Python, 453 JavaScript
(three skips) and 51 Rust tests pass. The subsequent relay changes need the
fresh full check and bundle qualification recorded after completion.

After the relay port, repository lint and all 768 Python, 453 JavaScript
(three skips) and 52 Rust tests pass. The coherent `rust-relay-bundle1` contains
435 files and includes the complete Rust sources and current build recipes.
Its direct executable is byte-identical to producer bundle2:
`5d4200841817e9039653861b67c38b3917bffd82d9c9e8cdaa68ebd03ac4e6ee`.

`rust-producer-lifecycle1` passes 60→30→60 FPS, fullscreen resize/restoration,
1.2-second source pause and receiver recovery with hardware H.264. Recovered
phases deliver approximately 60 unique pictures per second. Same-size graphics
context replacement remains outside this lifecycle check.

The same bundle also passes separate KMS/game 1080p240 H.264/24 Mbps recovery
checks under twenty CPU pressure workers and one GPU worker. Mean native
encoded rates during the constrained samples are about 235.5 FPS for KMS and
170.75 for game; recovery means are 239.5 FPS for each. These are single cases
and do not establish a throughput gain against the C producer. CPU marker
readback still limits picture-uniqueness observation below 240 FPS. No priority
policy change follows from these tests, and saturated-load frame loss remains
real. The game's single exported RGB lease is still an optimization target.

## 7 November: native encoder adapter and X11 cursor

The 572-line C encoder adapter was removed. Its replacement is
`native/bridge/src/encoder_adapter.rs`, separate from the private FFmpeg backend.
Bounded Rust handles replace the linked list and manual user counts. The adapter
no longer holds an outer serial mutex across encoded callbacks: the private
backend serializes codec work and supports callback-driven update/destruction.
Adapter admission still waits for operations on other threads before destruction
returns. Reentrant destruction closes admission and lets the current operation
unwind. Private backend ABI 2 makes those concurrency guarantees explicit; older
backend tables are rejected while native codec capabilities remain available.

A retained module guard checks executable bounds, exact exported targets,
accessor symbol starts, slot alignment and current page protections. A shared
GOT transaction validates every slot before any write, restores page permissions
and rolls back on failure. Original callbacks, backend and module stay retained
even if rollback cannot restore every page. The native by-value ABI is generated
from the shared header. Only the remaining encoder addon's embedded-image
boundary passes the immutable image pointer/length into Rust.

The new fixture loads a separate native ELF and private backend DSO, then runs
the actual patched GOT calls. It covers foreign forwarding, retained/stale DPE
handles, GPU import failure releasing its lease, CPU fallback, cross-thread
destruction waiting for update, same-callback update/destruction, the 128-session
limit, wrong slot/accessor offsets, missing backend, ABI 1 rejection and a
camera-only build. It does not inject production globals or bypass the installer.

`rust-adapter-gpu-lifecycle` passes HEVC/AV1 suspension, resize, recovery,
callback reentry and concurrent destruction: 21 generated pictures and one
independently decodable recovery picture per codec. `rust-adapter-kms1` delivers
1,798 unique 720p60 AV1 pictures over 30 seconds, with 24 ms median source-to-CPU
receiver latency and zero full-resolution CPU readbacks.
`rust-adapter-wayland-game1` delivers 717 unique pictures over approximately
12 seconds, 18 ms median latency and zero full-resolution CPU readbacks. These
are delivery checks, not evidence of a CPU improvement from the adapter rewrite.

Opaque native frame, capture, encoder and planar handles now have separate
reserved namespaces. Retired DPE handles remain recognizable after removal and
cannot be forwarded into proprietary frame accessors/destructors as native
allocations. Recognition grants no ownership. Ordinary scalar JS IDs and
timestamps keep their numeric representation. `native-pointer-kms2` and
`native-pointer-game1` pass real GPU loopback with these handles: 718/719 unique
720p pictures, 23/18 ms median latency, no invalid/out-of-order markers and no
full-resolution CPU readbacks. Foreign/stale-handle regression fixtures also pass.

The X11 cursor implementation is now `native/x11/`; the former C header contains
only the ABI. System XCB/XFixes libraries remain optional and dynamically loaded.
Rust owns the connection, loader references, reply/error/event allocations and
actual-size aligned metadata. Handles are bounded and confined to their creating
thread. No extra polling thread or PipeWire event loop is used. Cursor image
requests follow shape events; geometry follows the initial query and configure
events. Only pointer position is polled, at most about 62.5 times per second.
Unmap/destroy, invalid geometry, protocol errors and overdue requests hide the
cursor. Replies from an older shape generation cannot overwrite newer state.

The private Xvfb fixture uses an owned window and generated 2×2 cursor. Across
120 updates spaced approximately four milliseconds apart it observes 30 pointer
requests, zero geometry requests and zero image requests after initial setup.
Shape replacement, resize, hide/show, destruction and stale-handle cleanup pass.
The Rust unit test verifies payload size, hotspot and position bounds. The GPU
sprite fixture still checks premultiplied alpha and clipping when run with an
explicit render node. Removing the fixed 256 KiB cursor array reduces reserved
producer storage; whole-process RSS/PSS savings require matched measurements.
Six alternating 30-second game runs now compare the previous C cursor producer
with this Rust producer, using the same Native8 encoder helper and the frozen
offline runtime. All pass, with 1,796–1,799 unique pictures and 18–19 ms median
latency. Mean direct-producer PSS is 8.160 MB versus 8.035 MB; combined broker and
producer PSS is 19.061 MB versus 19.022 MB. Sender-plus-helper CPU increase over
the corresponding idle-call baseline is 3.807 versus 3.907 percent of one core.
CPU ranges overlap, and RSS/sender PSS vary. These runs establish no CPU saving
or overall pipeline RAM saving. The small producer PSS change is not attributed
to Rust as a language. Raw evidence is `x11-matched-summary.json` under the
private evidence directory; the interactive checkpoint is
`out/capture-x11-cursor-20261107.html`.

Managed `capture-rust-native8` was assembled from the newer retained runtime and
current working-tree helpers. Separate `native8-runtime-kms1` and
`native8-runtime-game1` checks use that actual managed executable, helper and
capture bundle. Both pass AV1 720p60 GPU loopback. This does not replace a live
Discord call on Native8. Verification subsequently cleared, and the existing
approved Native7/Clock1 call was reused for the reverse check described below.
Repository lint and 766 Python, 453 JavaScript and 44 Rust tests pass; three
JavaScript tests are skipped. The first full test/staging attempt exposed a
removed transitive `time.h` include in the remaining C direct producer; the
explicit include was restored and the complete suites and bundle rebuild pass.

Full migration remains incomplete: 15 project-owned runtime C/C++ files still
contain approximately 9,662 lines, excluding C header implementations and vendor
code.

## 7 November: Rust encoder backend

The private VAAPI backend is now `native/encoder/`; the 834-line project-owned
`hevc-backend.c` was removed. The upstream FFmpeg archive and its reviewed rate
patch remain pinned C dependencies. This is another migration checkpoint, not
completion of the remaining native capture, producer and adapter code.

Frames, packets, contexts, frame pools, imports and producer leases have scoped
owners. Session handles use bounded, never-reused tokens. A separate admission
guard lets destruction wait for foreign operations and callbacks; encoded/drop
notifications run outside the codec mutex. A callback may update or destroy its
session. Reentrant destruction closes admission and cancels further callbacks,
so callback context can be freed on return. Input-release functions remain leaf
cleanup callbacks. Codec reconfiguration frees old hardware/producer references
before reporting cancelled pending inputs. Packet storage is recycled without
copying encoded payloads during normal delivery. Checked timestamp arithmetic
rejects exhaustion rather than overflowing a signed counter.

The package builder generates bindings from the exact private FFmpeg headers,
runs feature-specific Clippy with warnings denied and retains existing FFmpeg
objects. The linked library's version script exports only the four intended DPE
entry points. No system libavcodec is loaded into Electron. Complete Rust source,
notices and the FFmpeg source archive remain in the package rebuild inputs.

`rust-encoder-lifecycle2` passes real GPU HEVC/AV1 suspension, resize, resume,
rejected-update recovery and crop transitions, with 21 decoded pictures and an
independently decodable recovery keyframe per codec. The extended fixture also
passes callback-driven update/destroy, stale handles, exact lease release on
failure and a concurrent destroy that waits for an active callback. Separate
180-frame DMA-BUF runs pass H.264, HEVC and AV1: one import, 179 cache hits, zero
remaining imports and zero full-resolution CPU readbacks each. These short runs
establish ownership and delivery; resource comparisons still need matched runs.

Managed Native6 also passes an approved KMS call after a source-picker automation
timeout on the first attempt: the retry advances its 320×180 preview and receives
600 remote 1920×1082 pictures in about ten seconds, without 2011. The initial
timeout is retained; both calls disconnect. Native6 predates the encoder-backend
port and therefore does not qualify that later library in a live call.

Managed Native7 subsequently passes approved KMS and game calls using the Rust
encoder backend. KMS receives 631 pictures in about 10.5 seconds; the game
receives 299 in about ten seconds at its negotiated 30 FPS. Both previews
advance and neither sender reports 2011. A separate current-helper clock version
was assembled. Its initial join timed out before another manual verification
request. Once verification cleared, the existing approved call was reused.
Clock1 initially had experimental capture disabled; it was enabled through DPE
Settings. The first reverse KMS start failed before frame delivery, and the
second attempt selected a source without completing Discord's additional stream
confirmation. Both failures are retained. The local script now handles that
confirmation and discovers the active Electron isolated context instead of
assuming its ID is always two. `rust-clock1-kms-resumed4` receives 308 pictures
at 768×432 over about 10.3 seconds, reports no 2011, and closes the source cleanly
after 344 producer deliveries. Both profiles were disconnected after the test.
This qualifies reverse KMS delivery on Clock1, not a live Native8 call or visual
quality parity. All peer identifiers and call automation remain ignored/private.

## Rust cursor connection

`native/wayland/` replaces the 470-line Wayland cursor implementation with typed
Wayland objects on a dedicated connection. The direct producer no longer creates
a PipeWire loop for cursor events. Its linked dependency inventory contains
neither libpipewire nor libwayland-client. The older PipeWire fallback producer
retains its video-stream event loop. Cursor metadata calls perform bounded,
nonblocking socket dispatch; static cursors leave one damage-paced request
pending. No helper polling thread or main-window frame capture is introduced.

The compositor receives a size-sealed memfd, retained by its cursor buffer.
After a completed cursor frame, `pread` copies only that bounded image into
owned aligned storage. There is no CPU mapping aliasing compositor writes.
Storage grows to actual cursor dimensions rather than allocating a 256 KiB
metadata array on startup. Constraints changes retain the old buffer until its
frame completes; ambiguous seats, changed globals and unsupported geometry stop
cursor delivery. Opaque handles are bounded and never reused. Returned metadata
is valid on its creating thread until its next call or destruction.

Rust tests cover pixel/header layout, hotspot-image atomicity, negative cursor
positions, source coordinate changes and allocation bounds. The private C
compositor fixture exercises the actual Rust protocol connection, demands exact
source selection, forbids main-window frame requests, changes constraints while
a frame is pending and destroys an outstanding cursor capture. It passes, as do
the 20 producer/backend checks and a direct-producer build. Live native-Wayland
game cursor delivery and matched resource qualification remain pending.

## Encoder CPU investigation and shutdown bookkeeping

Six alternating KMS AV1 runs compare retained C backend/helper12 and Rust
backend/helper13, each at 1280×720/60, 6 Mbps, 30 seconds plus ten seconds idle.
All pass. Sender-plus-helper CPU increases average 4.410 and 4.508% of one core;
the ranges overlap. Median latency is 22–24 ms, with 1,799 unique pictures in
each C run and 1,799/1,799/1,797 for Rust. This does not establish a language
rewrite performance improvement.

Eighteen separate hardware import/encode runs submit 3,600 synthetic frames per
case, rotating C/Rust order across H.264, HEVC and AV1. Every case releases all
imports without full-frame CPU readback. Per-stage AV1 CPU timing puts roughly
49 microseconds per picture inside FFmpeg submission, about 0.7 in descriptor
import and about one in the encoded callback. C's drain counter includes its
callback; Rust dispatches outside the codec mutex, so subtract that nested
counter before comparing drain costs. Hardware `perf` sampling and software
sampling are unavailable under the host policy; software counting works. No
kernel permissions were changed.

The first Rust admission guard notified a condition variable on every picture.
It now keeps bounded reentrant depth in thread-local storage, uses an atomic
closing flag and wakes waiters only during closure. Actual 180-frame main-thread
syscall traces show 341 wake-all futex calls before and 161 after; 160 occur in
the C reference. Driver operations contribute the remaining wake-ups. Callback
reentry, concurrent closure and independent/out-of-order admission tests pass.
Repeated CPU measurements still vary enough that no percentage CPU saving is
claimed for this change.

Helper13 also strips private-backend debug data before embedding it. Its backend
image is about 1.7 MiB versus helper12's 5.5 MiB. The loader writes the complete
embedded image into a sealed memfd, so debug sections cause real startup copying
and backing-store allocation even though the dynamic linker does not load them
as executable segments. The observed sender PSS difference cannot be attributed
to Rust alone.

Three repetitions per priority mode under CPU plus GPU pressure all pass.
Constrained native encode FPS averages 171.5 inherited and 174.6 balanced, but
individual runs overlap and one balanced repetition is worse. Capture-thread
runqueue wait decreases in all paired repetitions, without establishing a
consistent throughput improvement. Recovery returns to the requested rate.
Balanced priority remains an explicit diagnostic opt-in; normal scheduling is
still the default.

## GPU memory visibility

The benchmark now retains DRM total/shared/resident/active/purgeable memory by
client and region, converts the kernel's byte/KiB/MiB units and normalizes AMD's
legacy `drm-memory-*` alias to resident memory. Duplicate descriptors and
inherited client IDs are counted once within a group. Shared buffer imports
still overlap other clients/groups; categories and regions are not a physical
memory total. Separate sysfs samples retain whole-device VRAM/GTT use when the
driver provides it. Unavailable counters stay absent. Engine counters now retain
their previous high-water mark across temporary driver dips, as specified by
the [kernel DRM statistics interface](https://docs.kernel.org/gpu/drm-usage-stats.html).

`rust-cursor-wayland-game1` passes a native Wayland/EGL owned workload through
the coherent Rust-cursor bundle and helper14: aligned AV1 720p60/6 Mbps, 716
unique decoded pictures in about 12 seconds, median 19 ms, zero direct failures,
zero full-resolution CPU readbacks and complete teardown. Its sender reports
about 58.2 MB resident VRAM, including 8.3 MB shared buffers, plus 12.5 MB GTT.
These are client accounting observations, not additional physical allocation.
The broker currently resolves native cursor identifiers only through trusted
Sway IPC; this niri run qualifies video delivery rather than host cursor overlay.

`rust-cursor-kms-pressure1` passes 1080p240 H.264/24 Mbps with simultaneous CPU
and GPU pressure. Constrained native encode observations are 232/236/233/233 FPS;
recovery is 240/239/240/240. Direct failures and full-frame CPU readbacks remain
zero. The sender reports about 94.2 MB resident VRAM, of which 21.2 MB is shared,
and 16.2 MB GTT. This single case is a stress/ownership qualification and cannot
be compared as a memory saving against the different-codec 720p case above.

## Error 2011

The original optimized sender already transmitted AV1 to its remote viewer.
The failure affected Discord's local output stream: its preview requested I420
synchronously during the capture callback, where the direct adapter deliberately
declined full-resolution GPU readbacks. No preview frame arrived before its
2011 timeout. Picker thumbnails used a separate path and continued working.
A session without a watching receiver also discards encoder-queue frames; that
counter alone does not establish a capture failure.

The Rust preview hook recognizes only DPE-owned native GPU frames at the
reviewed DirectOutputStream callback. It retains an independent NV12 lease,
queues the latest request, downsizes on a worker and reads back at most 320×320
at 10 FPS. Immutable I420 pixels go to the public Electron video-output SDK.
Foreign frames retain their original callback. Queue capacity is one; two CPU
buffers and four input imports bound memory. Idle timeout releases the GPU
context. Potentially unfinished GPU work quarantines one source lease and stops
that preview worker rather than authorizing premature source reuse.

The prepared native module remains scoped to SHA-256
`7891b5ef54457b80df036a8b38d59045e9dbcca881d8c8b66f29e88fd050f479`.
Scope generation checks the output vtable, VideoFrame buffer accessor and
Wumpus handle accessor. Installation verifies pointers, bytes and permissions.
An initial prototype exposed duplicate Node cleanup-hook registration; the
replacement registers once per environment and shuts down after the last one.

Two explicitly approved account profiles completed live calls. Game capture
advanced the local 320×170 preview and remote 1920×1024 video. KMS advanced the
local 320×180 preview and remote 1920×1082 video. Neither sender showed 2011.
The automation ended both calls. Private account inputs, the reusable parameterized
call runner and reports remain under ignored `out/live-capture-private/`; no
account names or IDs belong in tracked source. Reports contain dimensions,
playback time and frame counts, without saved pictures or video.

`capture-rust-native1` also qualified the Rust worker, camera code and
profiling in live KMS and game calls. `capture-rust-native2` adds the Rust
full-resolution converter, relative preview priority and audio graph cache;
its game live qualification passes with advancing preview and receiver video,
zero full-resolution CPU readbacks and no 2011. KMS also passes live: 757
converted pictures, 749 GPU encoder inputs, 115 previews, zero preview errors
and no full-resolution CPU readbacks. `capture-rust-native3` adds the Rust native
frame bridge and stats registration. A human-verification dialog blocked its
first call attempts; the user completed verification, then both KMS and game
passed with advancing sender previews and remote video. The first diagnostic
probe preferred an inactive preload copy of the helper, producing misleading
zero counters. The probe now uses the active capture adapter's own stats function. It reports 824
converted/received frames, 781 GPU encoder inputs, 163 previews, zero
full-resolution readbacks and zero preview/capture errors across the two calls. Both use the retained verified runtime.

`capture-rust-native4` adds the Rust KMS client, metadata and server payload.
Its approved KMS and game calls both pass: local 320×180/320×170 previews and
remote 1920×1082/1920×1024 video advance without 2011. Both calls ended. The
installed privileged server remains the earlier binary; the new Rust server
payload was not installed by these tests. A manager switch first raced the old
manager's profile lock after Electron closed; waiting for the lock before
relaunching resolved it without altering profiles.

`capture-rust-native5` adds the Rust game hook, negotiated selected-output KMS
requests and a helper update action. The existing capture grant was retained;
the user completed the system authentication dialog. The installed server now
matches the bundled Rust payload, SHA-256
`dbb501829a48e316fef2ec78553fa41c10af7d7f549534cd23e920115d84f39a`.
An aligned 720p60 AV1 hardware sender/receiver case passes marker delivery and
cleanup. A subsequent approved live KMS call advances the local 320×180 preview
and remote 1920×1082 video without 2011. The call ends cleanly. This does not
resolve the separate strict AV1 1080-line padding-quality issue below.

Runtime identities differ between offline benchmarks and managed profiles.
The frozen offline runner uses `versions/current/runtime/electron`, SHA-256
`4223184105a4ee9f162bbfefabbe3a4f0eccc239400d38f88fa56390dd3d945d`,
from compiler run 40053107555, commit
`b26c58ec6174c1cd750adc97c04a6e7c29a30033`. Its provenance has three follow-up
patches through osr-child-views. Managed Native7/Native8 and Clock1 use retained
compiler run 41282631878, commit
`4c8eb42c0ce0ddd38b52752572043413938b17d9`, executable SHA-256
`6d0e3db61278f7be23afbb0129361d6fe4d30c9547e7fe5d1248740b9af57cb5`,
with five follow-ups through window-controls. An earlier version of this report
incorrectly paired the offline executable hash with the newer run's provenance.

Neither retained runtime carries the later presentation-clock,
AV1-crop-metadata or media-content-only source ports now in the working source
lock. Building newer helper versions does not compile those Electron changes.
Hardware-decoder crop-proposal support cannot be inferred from the current
source patch or from a newer helper build. Matched offline comparisons keep
their own runtime constant; live-call results qualify the separate managed
runtime and must not be merged into those resource measurements.

## Rust ownership and build integration

The workspace pins Rust 1.99.1, edition 2024, rustfmt and Clippy. Cargo's lockfile
pins dependencies; unsafe operations require explicit blocks and explanations.
`dpe-core` forbids unsafe code. Current modules cover:

- A bounded latest-item queue with explicit ownership on replacement and close.
- Preview GPU resources, input leases, immutable pixels and Node registration.
- Ancillary descriptor transport using OwnedFd, including malformed-packet cleanup.
- Direct capture lifecycle using owned sockets, Arc state, a joined worker and
  monotonic opaque tokens. Reentrant destruction retains worker ownership through
  callback return; no pointer to a freed capture allocation is dereferenced.
- Camera capability probing, conversion mirroring and the addon entry point.
  The stable Rust ioctl interposer uses a nine-byte SysV tail trampoline to pass
  the original caller address into the Rust implementation. Mirroring reverses
  bounded row slices after successful conversion and preserves row padding.
- Opt-in aggregate wall/thread CPU counters with no source identity or content.
- Full-resolution VAAPI conversion with Arc-owned surfaces and DMA-BUFs, four
  output slots, eight pinned imports, checked clipping and monotonic frame/lease
  tables. VPP parameter buffers and fallback readback images/storage are reused.
  Failed completion retains the affected context and blocks further creation.
  The real-GPU fixture verifies colour, cursor clipping, neutral allocation
  padding, pool pressure and leases surviving capturer destruction.
- Native GPU scaling and synchronization adapters. Pure Rust policy validates
  optional pixel limits, even reductions and saturated monotonic deadlines. A
  shared startup patch guard retains the loaded module, checks RX segment bounds
  and exact call/context bytes, and owns uncommitted trampoline mappings. C++
  padding is never read as initialized Rust bytes. Loaded-ELF tests execute the
  actual patched SysV calls, including the scaler's two-register struct return,
  wrong offsets/targets, idempotence, errno and opaque native error pointers.
- AV1 software-decoder view adapters and a safe bounded metadata parser. Each
  decoder has owned state, a bounded reordered-frame table and independent
  locking. Only metadata scalars and validated view pointers change; native
  allocation/reference tails remain untouched. The real six-site call/jump
  fixture verifies retries, reorder, context reuse, 128-context limits and all
  close paths against the Rust implementation. Geometry tests verify that an
  invalid third plane cannot partially move earlier planes.
- Encoder import caching through opaque monotonic leases. Arc-owned imports pin
  their DMA-BUF and device; cache closure removes lookup ownership while queued
  frames remain valid. Four slots and bounded ownership tables replace raw entry
  pointers and manually maintained user/reference counters. Invalid or repeated
  release cannot decrement another frame. Foreign device callbacks serialize,
  and descriptors close before final device cleanup. The retained-lifetime C
  fixture and actual H.264, HEVC and AV1 GPU encoding fixtures pass, including
  descriptor reuse, layout changes, full pinned caches and concurrent releases.

The remaining DPE producer GPU, picker, encoder, GL synchronization, Wayland and compatibility
helpers still contain C. The separate GPL-3.0 KMS Rust crate now replaces client
startup/authorization, protocol transport, scene policy, inventory, metadata
inspection and the privileged server/exporter. It does not link GPL code into
the MIT addon. Its protocol parser validates C bool representations before
creating typed records and immediately adopts all ancillary descriptors.

Client startup uses `posix_spawn`, owned sockets and monotonic session tokens.
It serializes temporary cancellation handlers, preserves the default SIGCHLD
ownership contract, and closes the connection before waiting on the helper.
A bounded child permit follows any delayed background reaper. Repeated cleanup
cannot close a recycled descriptor. Tests execute real spawn file actions and
local peer/handshake sockets while replacing only privilege/exec boundaries.

The Rust exporter caches immutable property names/IDs, fetches current values
with one bounded object-properties ioctl, and refreshes topology IDs every
request. It avoids the old per-frame property-name and connector-description
queries. GETFB2 remains fresh; repeated GEM handles are closed once, and partial
PRIME exports drop all already-created FDs. Protocol serialization writes named
fields into zeroed bytes, including alignment padding. Test-only libdrm symbols
exercise descriptor ownership, stale-property rejection and topology changes;
they are absent from production. The pinned upstream exporter remains an
attributed reference, without being compiled into the Rust server.

Before the native5 helper update, the rebuilt Rust client passed a real
1080p60 GPU sender/receiver case against the previous C server with 723 producer frames,
zero producer drops, valid decoded markers and clean teardown. Its metadata
checks average 0.0266 ms wall / 0.0264 ms caller CPU across 723 calls; this is a
single diagnostic run, not a repeated performance claim.

The selected-output extension is negotiated by an exact capability tag in the
initial response. A distinct DPE request type carries the connector ID; ordinary
GSR GET requests continue to ignore their unused final word and return all
outputs. The exporter skips unrelated planes before GETFB2, PRIME export or HDR
blob lookup. Legacy servers and unknown capability suffixes use the original
all-output request. Descriptor and topology fixtures cover both paths.

The permission status compares the bundled and installed helper hashes using
bounded, no-follow reads. An existing compatible grant remains usable while
Settings offers Update helper. Updating uses the existing installation action
without revoking the grant; cancellation retains the old usable helper. The
focused real settings harness passes update, cancellation, keyboard and focus
cases. Its runtime-copy preparation also replaces a copied resources symlink
before assembling fixture resources, protecting the verified input tree.

Existing C boundary, authorization/cancellation and descriptor fixtures pass
against the Rust archive. Direct producer, KMS producer and monitor-preview
builds also pass. This is not a completed all-native migration. The audio
adapter currently uses Python/JavaScript plus reviewed upstream binary patches;
there is no separate project-owned audio C helper in the integration inventory.
Pinned upstream sources retain their source languages and notices.

Repackaging source/notices requires no Rust installation. Build-time bindgen
reads system VAAPI/DRM, Linux and Node headers; generated bindings stay in build
output. The distribution retains Cargo inputs, helper source and pinned runtime
notices. See [third-party notices](../../THIRD_PARTY.md#rust-native-support).

## Rust game producer control

`native/game/` replaces the DPE game-hook control helper. It uses one owned
seqpacket socket, a bounded current lease and a retired release token, explicit
128-byte serialization and mutex-protected state. Published storage stays held
until its exact generation/sequence release. Source reconfiguration, stale ACK,
failed direct allocation, sync-file ownership and cancellation cross the actual
Rust/C boundary in the existing synthetic fixture. Pacing keeps phase, admits
at most 1 ms early, and never sleeps or queues capture credit.

The coherent `rust-game-hook-bundle2` builds both opted-in hooks and passes the
native tests. Its supplied corresponding source also rebuilds independently
using its own Cargo inputs. Section garbage collection and local archive symbols
keep the hooks around 484/493 KiB; removing debug sections changes disk size,
not a claim of proportional RAM savings. The managed native4 version predates
this game-hook port.

Native5 includes that hook and the installed Rust KMS server. A later native
helper containing the Rust scale/synchronization adapters also passes a split
receiver 1080p240 H.264 game-capture case. Its encoded/decoded rates reach the
requested rate, but only 1,442 unique CPU picture samples arrive in about
12 seconds. Separating receiver processes does not remove that approximately
120 FPS diagnostic ceiling. This remains a throughput result rather than proof
of 240 individually verified decoded pictures per second.

## Native verification memory

Longer split-peer runs exposed an initialization artifact: whole-module hashes
used separate `fs.readFileSync` buffers in each adapter. The voice module is
126,129,456 bytes. The picker added another such buffer to optimized methods;
V8 released these external allocations at different times during capture.
Early idle-to-active RAM subtraction was therefore not a valid steady memory
comparison. The initial sender RAM deltas and chart must not be interpreted as
established overall savings.

Camera and all six video guards now share bounded 64 KiB streaming SHA-256
verification. A 32-entry digest cache keys stable device/inode/size/mtime/ctime
identities; each use checks the open descriptor and pathname. Unknown hashes,
symlinks, changed files, truncated reads and oversized files fail closed. No
module bytes remain in the cache. Packaging aliases share the same verifier;
source fingerprints and prepared-client identities include it.

Three alternating-order standalone Node repetitions each execute seven checks
of the actual module. Previous checks use 495.8–501.3 ms CPU and about 305 MB
peak process RSS. The bounded shared verifier uses 63.4–64.0 ms and 54–55 MB.
Immediate proportional-memory increase falls from about 253 MB to 3.1 MB.
These numbers describe initialization, not ongoing capture. The corresponding
interactive artifact is `out/native-verification-cost-20261106.html`.

A first rebuilt-helper KMS AV1 720p60/6 Mbps run passes 1,799 unique pictures
in 30 seconds with valid markers and cleanup. Sender PSS averages 105.1 MB
during capture and 92.2 MB idle. Earlier long KMS runs averaged 358–476 MB
active and 472 MB idle. A new matched series is needed before quoting relative
memory savings; the new Rust decoder adapters also differ between those builds.

The decoder rewrite also retains input metadata for repeated output pictures
until its bounded ring slot is replaced. Removing a snapshot after its first
output, as the C adapter did, could leave a private ticket in a second output
from the same input. A repeated-output fixture checks timestamp restoration and
view cropping independently for both outputs.

## Scheduling experiment

The opt-in `--capture-priority balanced` benchmark mode requests nice −2 for
only the DPE capture worker if its inherited priority is −1/0 and the user's
existing RLIMIT_NICE allows it. It preserves explicit background priorities,
stronger inherited priorities, scheduling policy, affinity and all resource
limits. Permission failure retains the previous priority; capture keeps working.
Production behavior remains inherited pending matched pressure measurements.

The first 1080p240 game pressure pair verifies nice 0 versus −2 on the actual
capture thread. Both recover to 240 FPS after the bounded CPU/GPU load. During
pressure, native encode samples are 156–181 FPS inherited versus 166–190 with
the preference, but total sampled runqueue waits are similar, about 152/150 ms
over approximately 3.4 seconds. One pair cannot attribute the FPS difference to
scheduling; repeat in alternating order before adopting it as a default.

An EEVDF custom time slice would require `sched_setattr`. The upstream Chromium
[baseline policy](https://chromium.googlesource.com/chromium/src.git/+/bb1e17a6bdf1bedca8141d84d2593862981b46d7/sandbox/linux/seccomp-bpf-helpers/baseline_policy.cc)
and [syscall classification](https://chromium.googlesource.com/chromium/src.git/+/bb1e17a6bdf1bedca8141d84d2593862981b46d7/sandbox/linux/seccomp-bpf-helpers/syscall_sets.cc)
treat that syscall as watched advanced scheduling. This is an upstream reference,
not qualification of the pinned renderer policy. No such syscall is attempted
in the live renderer and no sandbox restriction is weakened.

Instrumented 1080p240 H.264/24 Mbps cases both pass marker delivery and cleanup.
The baseline publishes 2,881 frames, acknowledges 2,880 and observes four
presents while its source lease is held. Mean observed admission-to-ACK time is
4.11 ms, including the application's next-present polling granularity. Under
four seconds of CPU+GPU pressure, it publishes/acknowledges 2,584 frames, observes
301 held presents and records zero GPU-busy cancellations. Mean lease time is
4.58 ms, maximum 45.8 ms; native encoder rates during pressure are 157–180 FPS,
then recover to approximately 240. These are single diagnostics, not repeated
performance estimates. The producer remains one buffer; loss under pressure is
not fixed. Conversion VPP waits rise from 0.077 to 0.477 ms mean, with a 4.89 ms
maximum. Callback wall-time outliers also indicate scheduling delay needs
separate measurement before blaming GPU execution alone.

Opt-in resource sampling now records main/DPE thread nice values and
`/proc/TID/schedstat` runqueue wait, separating runnable CPU delay from blocked
GPU/IO time where the kernel exposes counters. This adds diagnostic overhead;
profiled runs remain excluded from small percentage comparisons. The host is
CachyOS 7.3.1; tool-launched tasks inherit nice −8 and RLIMIT_NICE 30. These are
host-specific conditions, not default Linux priority behavior.

## Initial stage measurements

Evidence is retained under `out/live-capture-private/`, including all failed
attempts. `kms-profile1` and `game-profile1` use the same frozen helper and coherent
capture bundle, a real 240 FPS owned 1080p GLX workload on DP-2, requested 1080p60
H.264 VAAPI at 6 Mbps, normal delivery and a local native receiver. Each runs for
12 seconds. They pass hardware encoding, decoding, marker integrity, selected
capture, zero full-resolution CPU readbacks and teardown. These are instrumented
diagnostic runs, not a repeated performance comparison.

Mean inclusive time per call, in milliseconds:

| Stage                       | KMS wall / CPU | Game wall / CPU |
| --------------------------- | -------------- | --------------- |
| Whole capture conversion    | 0.201 / 0.063  | 0.160 / 0.058   |
| Capture VPP completion wait | 0.112 / 0.007  | 0.079 / 0.006   |
| Encoder DMA-BUF import      | 0.002 / 0.002  | 0.002 / 0.002   |
| Encoder submit              | 1.778 / 0.123  | 1.769 / 0.118   |
| Encoded callback            | 0.071 / 0.070  | 0.071 / 0.071   |

KMS request/response averages 0.191 ms wall / 0.018 ms caller CPU; metadata
inspection averages 0.034 / 0.034 ms. Server CPU is separate from the caller's
thread CPU. Nested scope times overlap and must not be added. Counters include
startup and teardown; resource samples use the shorter steady interval.
Kernel permissions deny call-stack profiling, including a self-owned perf child;
no privilege or sysctl changes were made.

The offline fixture still combines sender and receiver processes in its native
resource group. Summed RSS double-counts shared mappings; source/compositor
activity and host daemons require separate accounting. These timings do not
establish sender-only RAM/CPU or savings over PipeWire. Profile overhead must be
calibrated before using instrumented runs for small percentage comparisons.

A first AV1 attempt supplied an H.264-only decoder option and failed setup. The
next transmitted 715 unique pictures but failed framing qualification.
Selected-device VCN4 scope reporting is now fixed, but later runs still fail
strict decoded-padding checks (25 of 716 pictures in one run), despite neutral
Y=16/UV=128 padding verified at the exported GPU input. These runs remain
excluded: decoded padding and AV1 quality still need investigation. The 1080p VCN4 route already avoids a separate padding
VPP pass. Other window dimensions can still need it.

## Live resource accounting and audio notifications

Separate sender/receiver process-tree samples include proportional memory and
reaped-child CPU. In one game call, the sender's idle-call baseline used 40.02%
of one CPU core, versus 65.41% while sharing, with about 119 MB more PSS.
These are single-run diagnostic deltas; animated frontend and game state affect
them. The audio Python helper and its transient queries accounted for about
12.5% of one core, including 6.35% in the persistent helper itself.

The replacement retains bounded PipeWire Node/Client/Link metadata from
`pw-dump -m -N` and caches Pulse queries until relevant subscription events.
Introspection client events do not trigger a query feedback loop. Startup waits
for observed subscription readiness. Partial graph updates, watcher death and
invalid metadata cannot publish a stale cached snapshot. Selection, process
identity and route cleanup checks remain in place. A read-only host probe made
no query subprocesses in five steady snapshots after initialization.

Automatic PipeWire-Pulse and real PulseAudio fixtures pass all nine audio
selection/cancellation/lifecycle phases. The manual chooser fixture also passes.
Its first attempts failed in the test setup: GTK's GPU renderer could not import
buffers inside the isolated environment, then `wtype` was missing. The fixture
uses Cairo and an explicit test keyboard-tool path. Signed distro packages were
extracted under ignored output; no host audio services or packages were changed.
In the first live game comparison, the helper plus reaped query children fell
from 12.21% to 1.09% of one CPU core. Persistent helper CPU fell from 6.35% to
1.09%; steady query-child CPU disappeared. Total app CPU is noisier (65.41% to
63.47%, with idle-call baselines of 40.02% and 44.14%). Repeat matched runs before
claiming an overall percentage or RAM saving.

## Monitor-rate stress

At requested 1080p240 H.264 VAAPI/24 Mbps, all three methods reach approximately
240 native encoded/decoded frames per second without pressure. The receiver's
CPU RGBA diagnostic readback only reaches approximately 120 samples per second;
it cannot establish that every native decoded frame is unique.

A bounded four-second interval with twenty CPU workers plus GPU compute passes
recovery checks for all methods. Native encoder rates during that interval were
230–236 FPS for KMS, 139–178 for game capture, and 227–241 for PipeWire. These are
single runs, not repeated comparative estimates. The owned GLX source averaged
239.993 FPS in the game run, exposing a producer handoff/synchronization concern.
The Rust converter also passes KMS and game 240 FPS baseline cases with complete
marker coverage. The first Rust KMS attempt had zero valid markers because the
owned window was on an inactive workspace; the harness now focuses only that
window before capture. The failed run remains retained and excluded.

Exact picture uniqueness under contention needs a receiver method without the
CPU-readback ceiling. No frame-drop guarantee follows from these stress runs.

## GSR reference and remaining experiments

The official GPU Screen Recorder repository was cloned at
`f97d1e392816503282d20a0804c3ce3fd2943767`. Its VAAPI encoder exports encoder-pool
surfaces and imports their image layers once, allowing GPU conversion into the
encoder's reusable storage. This is a reference for reducing redundant work,
not evidence that DPE already has equal resource usage. No new GSR implementation
was copied into the Rust modules; existing GPL KMS protocol/server attribution
remains intact.

Next experiments need producer-specific costs, split sender/receiver accounting,
proportional memory and per-client DRM engines; actual unique FPS under bounded
CPU, GPU and memory contention; nonaligned encoder surfaces; and preview resource
policies. Scheduling must preserve regular unprivileged Linux operation.
[Linux nice values are per-thread](https://man7.org/linux/man-pages/man2/setpriority.2.html),
while stronger priorities depend on permissions and resource limits. Background
preview work now lowers only its own thread by five nice steps, capped at +5;
an already lower inherited priority is preserved. The operation is idempotent. No desktop compositor
priority, realtime policy, CPU affinity or privileged limit was changed.

## Checks so far

After the Rust GPU/frame bridge and audio cache changes, repository lint passes
and the full suite passes 757 Python tests, 447 JavaScript tests (three skips)
and 20 Rust unit tests before the game-hook port. Camera tests use fake drivers and synthetic pixels, including all sixteen
converter ABI arguments. Rust camera tests cover 648 dimension/rotation/stride
combinations plus conversion failure. Direct transport/worker fixtures cross the
actual Rust/C boundary with sanitizer checks on the C caller. Later edits require
another final check and managed live qualification.

After the native5 helper update and Rust scale/synchronization ports, lint and
all 761 Python, 449 JavaScript (three skips) and 27 Rust tests pass. The AV1
software adapter migration is subsequent work and needs a fresh check. Native5
is the running managed dev version; it predates those subsequent adapter ports.

After the Rust software-crop adapter, priority experiment and bounded verifier,
lint and all 761 Python, 453 JavaScript (three skips) and 33 Rust tests pass.
The subsequent repeated-output metadata correction still needs its own check.

The repeated-output correction and Rust import cache subsequently pass Clippy,
all 33 Rust tests, the C lifetime fixtures and real GPU H.264/HEVC/AV1 cases.
Repository lint and all 761 Python / 453 JavaScript tests pass as well. No
production asynchronous-depth change has been made.

## Matched results with bounded verification

`bounded-guard-av1720-*` repeats three alternating-order runs per method, each
with ten seconds of idle peer baseline followed by 30 seconds of capture. The
owned source renders 1080p240; the hardware AV1 sender outputs 720p60/6 Mbps to
the same isolated native receiver. All nine pass delivery, marker and cleanup
checks. This series uses the installed Rust exporter, selected-output protocol
and `profile-helper10`; the later import-cache port is qualified separately.

| Method      | Sender + helper CPU increase, % of one core | Including owned source + compositor | Sender active PSS | Median latency per run |
| ----------- | ------------------------------------------: | ----------------------------------: | ----------------: | ---------------------- |
| PipeWire    |                                       4.501 |                               7.921 |          117.4 MB | 30 / 31 / 33 ms        |
| KMS direct  |                                       4.400 |                               6.804 |          104.2 MB | 24 / 24 / 24 ms        |
| Game direct |                                       3.897 |                               6.929 |          104.8 MB | 18 / 18 / 18 ms        |

Actual sender-plus-helper CPU averages 6.967, 6.958 and 6.328% of one core
respectively. Idle sender CPU averages 2.466, 2.559 and 2.392%. Baseline-adjusted
sender/helper ranges are 4.369–4.673, 4.151–4.582 and 3.735–3.979. KMS's small
sender/helper CPU difference overlaps normal run variation. Its clearer observed
saving is compositor work; the four-group CPU increases are about 14.1% lower
for KMS and 12.5% lower for game capture. These are relative changes in the
listed work, not percentage points of whole-PC utilization.

Idle sender PSS is now stable at 91–92 MB. Sender memory increases by 26.1 MB
for PipeWire and about 12.9 MB for each direct method. Count other processes:
KMS helpers hold 1.4–4.0 MB proportional-memory bounds; game broker/source
helpers hold about 16.7 MB, and the game's process grows another 5.6 MB during
capture. Thus game capture does not establish lower overall RAM: those extra
processes can outweigh the smaller sender allocation. Host PipeWire/Xwayland
services, the portal controller, receiver and shared browser/GPU processes are
outside these totals; GPU allocations are not PSS. Reducing the game service
footprint remains worthwhile.

Unique decoded picture counts are 1,788/1,773/1,790 for PipeWire,
1,799/1,799/1,799 for KMS and 1,797/1,799/1,798 for game capture. Sender DRM
encode-engine counters average about 4.94% for every method. Their conversion
work appears on different GPU engines, so compute and graphics percentages
cannot be substituted for a measured device-wide saving. No compilers, tests
or pressure jobs ran alongside this series.

## Asynchronous encoder experiment

The cloned GSR's `src/recorder/video_codec.c` sets VAAPI `async_depth` to three,
explicitly trading more VRAM for performance. DPE currently uses one. An ignored
candidate changes only that option, records its source/binary overrides and
leaves production source unchanged. Real GPU tests cover all three codecs.
They expose two pending pictures discarded during shutdown; no pictures drop
before shutdown, and all leases/imports close. The candidate therefore needs
explicit stop/suspend/reconfigure treatment before any product option.

A first aligned AV1 KMS native loopback passes with 715 unique pictures in about
12 seconds, zero direct-capture errors and median source-to-receiver latency
55 ms versus roughly 24 ms at depth one. This verifies the expected latency
trade; it is not yet a repeated resource-use improvement.

## Matched AV1 comparison

`matched-av1720-*` contains three alternating-order repetitions per method,
using the same private AV1 backend, 1280×720/60, 6 Mbps, normal delivery and a
240 FPS owned 1080p GLX source on DP-2. All nine pass marker and teardown checks.
Native decoded dimensions are 1280×720, avoiding the unresolved VCN4 1080p
padding issue. No compilers or separate stress jobs ran during measurements.

| Method      | Observed CPU, % of one core (mean) | Median latency per run | p95 latency per run | Unique pictures / approximately 12 s |
| ----------- | ---------------------------------: | ---------------------- | ------------------- | ------------------------------------ |
| PipeWire    |                             51.901 | 34 / 34 / 35 ms        | 36 / 36 / 39 ms     | 709 / 714 / 706                      |
| KMS direct  |                             50.417 | 22 / 22 / 22 ms        | 27 / 25 / 26 ms     | 717 / 716 / 717                      |
| Game direct |                             49.447 | 19 / 18 / 17 ms        | 20 / 20 / 19 ms     | 717 / 717 / 717                      |

CPU totals sum the observed native sender/receiver, helper, source and compositor
groups. They omit host PipeWire/Xwayland service CPU and include the local
receiver, so they are not sender-only overhead. Within those groups, KMS uses
1.484 core percentage points less (2.86% relative), and game capture 2.454 points
less (4.73% relative). Latency includes receiver CPU output conversion, excludes
physical display scanout, and uses shared monotonic timestamps. Frame-marker
integrity does not establish equal full-picture visual quality. RAM savings
and older-machine behavior remain unproven. An interactive report is retained
at `out/capture-comparison-20261106.html`.

GSR's default AMD CLI mode enables `AMD_DEBUG=lowlatencyenc`; its low-power
option avoids enabling that flag. Mesa documents the option as
[low-latency encoding](https://docs.mesa3d.org/envvars.html), and its original
[implementation commit](https://android.googlesource.com/platform/external/mesa3d/+/6cc32c609bddeaa1997743425eb5d859a5ce74dc)
notes increased power consumption. DPE does not force this flag. The host already
reports a `high` GPU performance policy; it was left unchanged.

Six installed GSR 6.1.6 runs alternate three low-power and three default cases:
AV1 720p60/6 Mbps, performance tuning, the same 240 FPS owned source, 18 seconds
per run and the null muxer to `/dev/null`. All exit cleanly, reporting about
60–61 update/damage FPS. No encoded image/video file is saved. Recorder plus
child CPU averages about 2.65% of one core in both modes. The first run's PSS
bounds are 54.3–56.3 MB; the privileged child is not readable. Board power during
recording averages 65.2 W in low-power mode and 71.0 W in default mode. Per-run
increases over the immediately preceding owned-workload baseline are 7.0–8.8 W
and 12.9–15.1 W respectively. Board power includes the whole GPU and other
foreground workloads; these are host-specific reference readings.

GSR includes capture and encoding but no Discord transport, receiver, CPU pixel
conversion or Discord UI. Its process costs cannot be compared directly with
the native sender/receiver totals above. No GSR latency or full-picture quality
qualification follows from discarded output.
