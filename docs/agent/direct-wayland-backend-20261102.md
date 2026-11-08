# Direct Wayland producer integration

`integration/video/wayland-source.c` completes the existing output protocol
adapter with a standalone GPU-backed PipeWire producer. The shared DPE native
picker bridge and source-selection UI are integrated separately. The helper
does not replace the global desktop portal or modify compositor configuration.

## Helper contract

Build with `uv run python scripts/build_wayland_source.py --output out/wayland-source`.
The executable is `dpe-wayland-source`; its build report includes its hash.
Generated bindings use the checked protocol provenance, including the unchanged
Wayland Protocols 1.50 `linux-dmabuf-v1.xml` and its MIT notice.

`dpe-wayland-source --list` opens the current Wayland connection only to list
protocol support and output identities. It creates no capture session and opens
no GPU. It returns one JSON object with `version:1`, `backend:"wayland-direct"`,
`available`, `reason`, and `outputs`. Each output contains `id`, `name`,
`description`, `width`, `height`, `transform`, `supported`, and `reason`.
`id` is the exact `wl_output.name`, not a display index. Missing display or
protocols produce an unavailable result; no other capture method starts.

`dpe-wayland-source --output EXACT_ID --fps N` accepts 1–240 FPS. It inherits
`WAYLAND_DISPLAY`, `XDG_RUNTIME_DIR`, and `PIPEWIRE_REMOTE`, so integration tests
can supply private endpoints. Only the exact named output is selected. The
protocol adapter rejects ambiguous names and removal of the selected output.
The helper writes JSON lines:

```json
{
  "event": "ready",
  "backend": "wayland-direct",
  "nodeId": 42,
  "width": 1920,
  "height": 1080,
  "generation": 1
}
```

Ready means that the node exists; it does not assert delivered frames or an
active encoder. New constraints emit an updated generation. Runtime failures
emit an `error` event with a bounded reason string and stop. SIGTERM/SIGINT or
parent exit ends the owned helper. Startup has an independent eight-second
alarm; a running share does not have an arbitrary duration limit.

The supervisor can change the cap without changing the node by writing
`{"op":"rate","fps":30}` followed by a newline to the helper's stdin pipe.
The standalone helper also accepts `rate 30`. Commands are bounded and validated;
closing the supervising pipe stops the helper. Lower caps apply immediately;
higher caps wait for the negotiated PipeWire maximum. This avoids both a burst
at the previous cap and treating an asynchronous old format callback as a fatal
rate mismatch.

## GPU and ownership behavior

The compositor's advertised DRM device is resolved through libdrm to that same
device's render node. Primary/render node `dev_t` values are not compared as if
equal. A device change stops the source instead of silently opening another GPU.

The producer offers the compositor's supported BGRA/BGRx/RGBA/RGBx format and
explicit modifier combinations. PipeWire's modifier intersection is passed to
GBM for fixation, then the exact chosen format/modifier is used for every buffer.
Up to four planes are exported, with their actual per-plane stride and offset.
There is no implicit-modifier guess, CPU readback, pixel mapping or shared-memory
fallback disguised as GPU capture.

The compositor can advertise hundreds of unrelated format/modifier pairs. The
adapter filters those before adding the relevant combinations to its bounded
inventory. The first actual niri startup exposed this distinction: the original
unfiltered inventory overflowed before source creation. Relevant inventories
still fail explicitly when their capacity is exceeded; irrelevant formats cannot
hide supported RGBA choices.

The bounded pool requests four buffers within a two-to-eight range. At most one
capture request is outstanding. A timer grants one capture attempt at the
negotiated rate; delayed callbacks cannot accumulate capture work. Paused
PipeWire streams stop requesting frames. The compositor's `ready` event is the
readiness boundary, as specified by `ext-image-copy-capture-v1`; only then is
the buffer queued to the consumer. The producer cannot reuse it until PipeWire
returns it. Presentation timestamps are retained in `SPA_META_Header`.

Constraint changes invalidate old generations and renegotiate the existing node.
If PipeWire removes a buffer while the compositor still owns the destination,
the helper retains its Wayland buffer, BO and FDs until the frame callback or
dedicated connection teardown. Stale/failed frames are returned without delivery.
Shutdown disconnects the capture connection before freeing any remaining
outstanding storage.

## Supported scope and validation

This backend currently handles normally oriented monitor outputs. Non-normal
output transforms are disabled in enumeration and checked again on every frame;
they stop with `transform-unsupported` rather than producing rotated/wrong output.
Window capture stays on the portal route. Compositors without the required
protocols, GPUs with no shared advertised modifier, or DRM device changes fail
explicitly. These boundaries must remain visible in settings availability.

The focused `test_wayland*.py` run passes 20 checks, including the existing real
Wayland protocol fixture and new ASan/UBSan tests for pending-buffer retirement,
stale/failed-frame exclusion, successful metadata handoff, timer admission and
modifier offers. Argument tests and an unavailable-display capability check
do not open the host display/GPU.

A separate read-only `--list` check on the actual niri session confirms the
required protocols and three normal outputs. The following bounded delivery
checks used the actual selected `DP-2` output and a private PipeWire daemon:

- `wayland-direct-proof/report.json`: 120 valid 1920×1080 DMA-BUF frames with
  monotonically increasing sequence numbers, at a requested 30 FPS.
- `wayland-direct-rate-proof/report.json`: another 120 frames after requests
  of 30→15→30 FPS, with the same node and no producer error events.

The reports are under `/mnt/storage/dpe-capture-backends-20261102/`. The sink
checks descriptors and metadata without mapping or saving pixels. Both checks
stopped all owned processes; neither used an account or transmitted media.
The final tested producer SHA-256 is
`bca3dd51c6acee6f29aa5905fa62a1e7161cba38d4b7bf9396a21bd96bbadef8`.
These checks establish real compositor-to-PipeWire delivery and rate-change
continuity. They do not measure improvement under game load or establish pixel
correctness through a decoder.

## Native picker handoff

`capture-picker.c` and `capture-picker.cjs` connect an explicitly selected
external node to the existing native capture constructor. The scope generator
`scripts/capture_picker_scope.py` accepts only the reviewed native module
hashes and verifies the three exported C functions, relocation slots and
selected-configuration storage. Both the pinned 1.0.2357 native revision and
the newer native revision used by 1.0.2422 are explicitly reviewed, including
their HRD/dimension-corrected variants. The three C functions and configuration
get/clear bodies have identical normalized instructions; independent DWARF
inspection confirms the same four option/result/configuration layouts.
The loader rechecks the module hash, then patches the reviewed picker,
constructor and destructor linkage. A fresh module's canonical unresolved PLT
entry is accepted only when it jumps to that exact reviewed slot; arbitrary
pre-existing hooks are rejected.

The loader contract is `load(directory, helper, onEvent)`, returning
`finish(requestId, action, socketPath, nodeId)` and `cancel(requestId)`.
Events are `request`, `started`, `stopped` and `cancelled`, each with its own
request ID and source type. Actions are `native`, `selected`, `cancel` and
`error`. An external selection opens a same-user Unix PipeWire socket and sends
its owned FD through Discord's existing picker callback. Ownership transfers
before the native constructor starts its worker. Cancellation closes only an
unclaimed FD; destruction reports the exact session ID so an older capture
cannot stop a replacement helper.

Auditing `releaseNativeDesktopVideoSourcePickerStream` found that upstream only
closes its portal session; it does **not** clear the selected PipeWire
configuration. ABI 2 of the bridge therefore checks and clears the exact
matching FD/node under upstream's reviewed configuration mutex before closing
an unclaimed FD. A new picker also retires an old selection synchronously when
no capture constructor consumed it. Main-process timeout/cancellation sends an
exact request ID to the renderer, which calls scoped cancellation without
calling the global portal release. Duplicate, stale and active-session end
notifications cannot cancel a newer selection or close a transferred FD.

The ASan/UBSan fixture verifies stale completion, supersession, socket ownership,
transfer, independent capture destruction and the reviewed lazy-linkage check.
Its real Unix socket fixture covers selection cancellation without a consumer,
supersession and preservation of another selected configuration. JavaScript
tests verify hash/export checks, endpoint/event validation, selected timeouts,
late completion after cancellation and stale end events after a new selection.
The final parent integration runs `native-picker-final/report.json` (pinned
native revision) and `native-picker-current/report.json` (current corrected
`23dae…` revision) each passed all 14 checks. Both first selected and cancelled
a source without starting capture, then verified a new request's
request→started→stopped lifecycle and native capture→AMD AV1 hardware
encode→Electron hardware decode at 1280×720 using a synthetic private PipeWire
source. These reports are in the same evidence directory listed above.
That is a separate component proof from the direct-monitor descriptor checks
above; a single direct-monitor-to-native-decoder run has not been claimed.

Protocol and negotiation references are the pinned XML under
`integration/video/protocols/`, [PipeWire DMA-BUF sharing](https://docs.pipewire.org/1.4/page_dma_buf.html),
and the installed libdrm/GBM headers. The PipeWire contract requires allocator-led
modifier fixation and per-plane layout; the image-copy protocol makes `ready`
the point at which the image is readable.
