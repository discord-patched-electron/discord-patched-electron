# Native capture publication and delivery

The native capture bridge wakes delivery when a converted frame is published for
DPE-selected custom sources. Ordinary portal capture keeps its original delivery
unless explicitly enabled for comparison. This changes neither Electron nor the
game's rendering rate.

- `DPE_CAPTURE_DELIVERY_FAST=0` disables automatic fast delivery.
- `DPE_CAPTURE_DELIVERY_EVENT=0` disables publication wakeups and retains the
  240 Hz timer path for diagnosis. Missing optional scopes/helpers also retain
  that path.
- `DPE_CAPTURE_DELIVERY_PROBE=1` counts native callback identities without
  suppressing or delaying callbacks, including on custom sources. An explicit
  `DPE_CAPTURE_DELIVERY_FAST=1` enables fast delivery together with the probe.
- `DPE_CAPTURE_DELIVERY_FAST=1` enables fast delivery on both custom and ordinary
  native PipeWire sources for matched comparisons.

Fast delivery requests a 240 FPS bootstrap/fallback timer from the native capture
thread when the requested rate is between 1 and 239 FPS. Once the first callback
binds its opaque framebuffer, the thread sleeps until a publication, native
control update, stop, or 500 ms heartbeat. It forwards each fresh frame immediately, destroys
repeated frames, and forwards a repeated frame after 500 ms without delivery to
keep static-source/keyframe behavior possible. This heartbeat still needs live
keyframe-request validation.

The bridge only wraps native PipeWire captures with a supplied remote/node.
Ordinary captures keep their native portal session and FD ownership and emit no
custom-picker lifecycle events. Unknown CPU-buffer identities are forwarded at
the originally requested rate under FAST using absolute deadlines: callback
jitter does not reset their phase, and missed slots expire instead of producing
a catch-up burst. Errors and empty callbacks are always forwarded unchanged. A
zero/unsupported rate disables the fast behavior.

With an explicit PROBE or FAST opt-in, the helper writes
`[DPE capture delivery]` JSON to stderr approximately every two seconds and during
capture destruction. Automatic custom-source delivery emits no such logs. The
records include request ID, requested rate, fast-mode and `eventDriven` state, callback, fresh,
duplicate, unknown, forwarded, dropped and heartbeat counts. They contain no
pointers, pixels or source titles. Regenerate `capture-picker-scope.json` when
staging: this change uses picker ABI version 3 and optional publication-wake
ABI version 1. The JS loader exposes `eventDelivery` to report whether the
optional installer succeeded.

## Verified boundary and C ABI

The reviewed older module at
`/mnt/storage/dpe-capture-backends-20261102/current-voice/discord_voice.node` has
SHA256 `23dae94b2adcc295faa473fd5869b17cbc19154eec50d6cdf5970ef4f921b0c5`.
Its PipeWire conversion publishes an `Arc<LinuxFrame>` to a double-slot
`FrameBuffer`. A separate capture thread polls that buffer on an independent
`FpsTracker` deadline and dispatches its current frame without checking whether
it changed. This can repeat an older frame and omit an intervening publication.
The identity experiment distinguishes that behavior from corruption inside a
freshly converted frame.

| Boundary/export             | Older reviewed address | Pinned reviewed address |
| --------------------------- | ---------------------- | ----------------------- |
| `cc_linux_capture_update`   | `0xe2c300`             | `0xe5fda0`              |
| `cc_video_frame_clone_i420` | `0xe441c0`             | `0xe258b0`              |
| `cc_i420_buffer_destroy`    | `0xe44190`             | `0xe25880`              |
| `cc_video_frame_destroy`    | `0xe443f0`             | `0xe25ae0`              |

Pinned comparison used
`62812b1287d5ea13fd762e91c18a20532734080cd3b30f0059b538281c0c4f64`.
The four function bodies match after normalizing relocated code/data addresses;
the clone's remaining differences are logging-string locations. Scope checking
continues accepting only the existing six complete reviewed module hashes and
verifies each export's size, plus the update relocation.

DWARF on both revisions reports `FrameHandlerFn` as
`void (RsVideoFrame, uint32_t, const char *, void *)`, with `RsVideoFrame` an opaque
`void *`. The 40-byte `CcCaptureOptions` contains that callback at offset 16 and
its data at offset 24. Update copies the same options to its control queue.

There is no `cc_i420_buffer_get_data` export. Callee and C++ call-site disassembly
establish that `cc_video_frame_clone_i420` returns an opaque lease and writes six
outputs: data pointer, length, Y stride, UV stride, U offset and V offset. The
successful Linux GPU branch retains `Arc<PooledStagingBuffer>` and allocates an
eight-byte lease; it does not copy image data. Destroy releases that Arc. The
staging allocation is returned to its pool when the retained object drops.

The wrapper compares only the exported data pointer and retains the previous
lease until after acquiring the next lease. This prevents pool-address reuse
from being mistaken for a repeat. It reads no private Rust layout and does not
inspect pixels. Keeping a lease may retain one additional staging allocation;
benchmark its cost rather than treating the probe as free instrumentation.

Native capture destruction calls `LinuxCapturer::stop`, which joins the capture
thread before returning. Only then does the wrapper release the final lease and
callback context. JS environment cleanup leaves an active context alive until
native destruction. Existing capture errors and callback frame ownership are
preserved.

## Publication wake boundary

Five call sites replace the independent poll phase: the source's framebuffer
read, GPU and CPU publication writes, the capture thread's timed control receive,
and its overdue check. The wrappers pass opaque Rust pointers and scalar ABI
arguments to the original functions. They do not construct, clone or modify a
Rust object. The existing C callback supplies the capture context after a read;
thread-local storage associates that context with the exact framebuffer pointer.
Publishing calls the original write first, then signals only a matching active
capture. A single serial counter coalesces bursts, and the original framebuffer
still chooses its newest frame. No extra frame queue, image copy, worker thread,
or per-publication allocation is added.

| Call site              | Older address | Pinned address |
| ---------------------- | ------------- | -------------- |
| Framebuffer read       | `0xe799cd`    | `0xe8ee2d`     |
| GPU publication        | `0xe84f16`    | `0xe9a376`     |
| CPU publication        | `0xe83765`    | `0xe98bc5`     |
| Native timed receive   | `0xe2880e`    | `0xe5c83e`     |
| Delivery overdue check | `0xe28836`    | `0xe5c866`     |

The receive signature is `(output*, receiver*, uint64 seconds, uint32 nanos)`;
its original function returns that output pointer. The original caller checks a
64-bit all-ones tag and byte 8 equal to zero for timeout (one means disconnected).
Only this empty timeout result can be reused for a second zero-time receive.
Messages, including owned configuration payloads, pass through untouched.
Both native revisions have the same caller instructions and target signatures.
Full-file hashes, containing symbols, target sizes, adjacent opcode bytes and
resolved call targets are checked before installation. Unknown versions retain
the existing path; installation is rejected after any capture request starts.

The first zero-time receive gives native control messages priority. The capture
thread then waits on a monotonic condition variable until the publication serial
changes. A second native receive after waking handles a racing control message
before frame delivery, leaving its publication serial unread for the next turn.
The first callback schedules one immediate reread to close the initial
read-to-context-binding race. Configuration updates notify only after the native
queue accepts the update. Destruction marks the context stopping and wakes it
before native stop-and-join; that state restores the original interruptible
receive until stop arrives. The context and condition variable remain alive
until the capture thread has joined. Unrelated capture threads and unsupported
rates preserve their original receive behavior.

A publication wake bypasses the obsolete poll deadline for that one native
iteration. A 500 ms monotonic heartbeat preserves static-source delivery. This
removes the independent timer's phase delay and idle polling after binding; it
cannot remove GPU conversion, transport, encoding or receiver latency. Runtime
validation must establish that the scoped calls execute and `eventDriven` is
true before attributing performance to this mechanism.

## Producer rate and delivery rate are separate

In these exact reviewed native builds, changing `CcCaptureOptions.frame_rate`
changes the capture thread's `FpsTracker`, while PipeWire offers retain their
original rates. The outer capture thread copies that field into
`CaptureSourceConfig`, but `LinuxCaptureSource::new` drops it before spawning the
PipeWire thread: it copies only the portal fields and width/height. The source
configuration-message receiver likewise updates only width/height. Its format
builder accepts a pixel format and emits the constant framerate range
25 FPS preferred, 0–1000 FPS acceptable. No additional Rust hook is needed to
separate the two rates in these builds.

| Reviewed function                  | Older address | Pinned address | Size       |
| ---------------------------------- | ------------- | -------------- | ---------- |
| `LinuxCaptureSource::new`          | `0xe79c90`    | `0xe8f0f0`     | 1645 bytes |
| Configuration-message receiver     | `0xe7b910`    | `0xe90d70`     | 523 bytes  |
| `create_base_stream_format_object` | `0xe87b60`    | `0xe9cfc0`     | 1611 bytes |

The three bodies match instruction for instruction after normalizing relocated
addresses. In the older module, new copies configuration bytes 0–19 at
`0xe7a03e`–`0xe7a058` and leaves the frame-rate field at offset 20 unused; the
message receiver copies only width/height at `0xe7ba47`–`0xe7ba4f`. The frame-rate
field's name and offset are also visible in its native debug formatter. These
are evidence about the scoped binaries, not assumptions for later versions.

DPE's independent upstream adapter prefers the requested producer rate at the
initial PipeWire connection. This remains a preference, not a guaranteed cap:
it preserves fixed-rate producers, absent maximum-rate properties and the full
acceptable range. Default initial mode does not rewrite later format updates.
The game helper separately enforces the requested production cadence. Increasing
delivery polling does not set either of those controls to 240 FPS.

## Validation

`tests/screenshare/capture-delivery-check.c` includes the production bridge and
uses opaque fake frames and counted staging leases. Under ASan/UBSan it exercises
probe transparency, repeated identity handling, lease replacement/address reuse,
duplicate frame destruction, a static heartbeat, CPU fallback cadence, error
passthrough, changed FPS and callback data, environment cleanup, and a real
callback thread joined during native destruction. The original picker FD and
lifecycle fixture and JS loader test also pass. Both selected and ordinary
portal paths cover 60→30→60 FPS updates, failed queue submission preserves the
previous callback/rate, and failed ordinary capture creation leaves FD ownership
with the native caller.
Jittered 240 Hz callback sequences produce exactly 600/300 CPU fallback frames
over 10 seconds at requested 60/30 FPS, with at most one output per target slot.
A two-second scheduler stall expires missed slots without replaying a backlog.
Automatic selected-source mode stays enabled across 60→30→60 changes and emits
no diagnostic output; ordinary default delivery stays unwrapped.

These are offline ownership/cadence checks. They do not establish lower viewer
latency, compatibility with live keyframe requests, or an improvement under GPU
load. Paired native-path benchmarks supply the performance evidence separately.

The earlier timer-based instrumented runs confirm the identity measurement is useful.
They predate publication wakeups:
`relay-probe-gpu-02` produced 1199 callbacks, 1198 fresh identities and one repeat;
`relay-fast-gpu-01` produced 4794 callbacks with 1200 fresh identities and 3594
repeats discarded. Both decoded zero repeated frame markers. In their constrained
phase, decoded latency p95 was 46 ms in both runs, so that pair establishes no
latency improvement from fast polling. `portal-fast-gpu-01` also stayed near its
60 FPS producer rate while polling near 240 FPS: 1200 producer callbacks,
1182 converted images, and 1181 forwarded fresh identities. Its active native
diagnostics reported an upstream negotiated maximum of 60 FPS. These are single
runs under `/mnt/storage/dpe-capture-efficiency-20261102/`; repeated comparisons,
static-source/keyframe behavior and lifecycle coverage remain necessary.

`tests/screenshare/capture-wake-check.c` runs the production wrappers under
ASan/UBSan with a real publisher thread and native-result fixture. It covers the
poll-to-wait publication race, burst coalescing, unrelated source isolation,
control messages before and after wakeup, unread-publication preservation,
initial binding fallback, timer fallback, static heartbeat, and stop/wake/join.
These focused offline checks do not establish live viewer latency.
