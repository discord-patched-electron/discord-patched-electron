# Screensharing review, 5 November 2026

Scope: the complete current screensharing change set and its capture, native
module, encoder, receiver, settings and packaging dependencies. The starting
inventory covers 129 files; the final inventory includes 146 source, fixture,
patch and documentation files. These inventories identify review inputs, not
146 separately proven hardware configurations. Artifacts stay in
`out/screenshare-review-20261105/`.

The concurrent [Canary 2444 compatibility review](client-2444-compatibility.md)
owns the complete 2433→2444 upstream comparison. Its 39 changed payload entries
have explicit dispositions in
`out/client-2444-compat/full-inventory/dispositions.json`. This review uses the
actual prepared 2444 module rather than relying only on older qualification.

## Findings fixed

1. **Capture metadata inspection could crash.** `capture_dequeue` correctly
   declined to optimize unfamiliar buffers, but its subsequent diagnostics
   called `spa_buffer_find_meta_data` with a missing metadata array. A generated
   ASan reproducer crashed before the fix. Inspection now checks the pointer and
   bounds the inventory at 64 entries; supersession applies the same bound.
   The sanitizer regression also covers a non-null, over-limit inventory.
2. **An active replacement encoder could bypass another stream's advertised
   limits.** `stream_limit` started its ownership test with `dpe_hevc_active()`
   even when the current encoder metadata was ineligible or advertised explicit
   limits. The active-backend condition now also requires eligible current
   metadata. The regression exercises explicit limits, unrelated metadata,
   content types and codecs with and without a concurrent replacement backend.
   It failed before the guard change and passes afterward.
3. **Game capture disconnected on an FPS change after copy fallback.** An actual
   native game-capture lifecycle reproduced this: relay was requested, cursor
   composition required copying, and a subsequent 60→30 FPS control changed
   the hook's active allocation mode. The hook correctly rejected that change.
   The producer now retains its initial hook contract independently of its
   relay/copy consumer path. Existing leases, fences and hook validation remain
   intact. The source callback regression checks both rate-control packets;
   the complete real-game-source lifecycle passes after rebuilding the bundle.

No bitrate ceiling, congestion feedback, B-frame policy, receive buffer size or
hardware exclusion was changed by this review.

## Review coverage and retained decisions

| Area                                 | Review and disposition                                                                                                                                                                                                                                                                                                             |
| ------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Native capture adapter               | Dequeue/release ABI, bounded latest-frame scan, FIFO restoration, control/cursor metadata, per-thread timing, pacing phase and counters. Explicit-sync frames remain native; they are not treated as disposable raw frames. Fixed finding 1.                                                                                       |
| PipeWire negotiation, scale and sync | Optional framerate preference preserves fixed/unknown offers. Runtime renegotiation remains separately gated with timeout/restoration. Scale respects native sink wants and even dimensions. The exact duration-to-monotonic-deadline correction preserves native errors and ownership.                                            |
| Optimized picker                     | Request generations, cancellation tombstones, stale selections, exact source identity, descriptor ownership, native start/stop, TSFN rebind and environment cleanup. Picker dismissal preserves an accepted handoff. Unknown native shapes fail closed.                                                                            |
| Direct Wayland                       | Protocol inventory, advertised formats/modifiers, selected-output matching, transform rejection, generation changes, retired buffers, fenced completion, rate changes and cancellation. Unsupported transforms are not silently captured incorrectly.                                                                              |
| KMS                                  | Permission marker/version, polkit scope, root-owned paths, peer UID/PID, descriptor bounds, node/output identity, transient errors, retired buffers and release callbacks. Restricted launch environments use the existing checked user-service bridge; no privilege flags are cleared.                                            |
| Game hooks and producer              | Vulkan/OpenGL export, additive launcher variables, Steam runtime socket visibility, same-user broker, OBS compatibility, previews, bounded protocol parsing, GPU UUID/modifier checks, acquire/release, cursor composition, resize/retirement and rate changes. Fixed finding 3.                                                   |
| Encoder wrappers                     | Scoped ABI and full-module hashes, selected-vendor behavior, synchronous create/update, rejected updates, destruction during use, GPU surface leases and CPU fallback. AMD preset changes remain limited to Mesa AMD H.264. Fixed finding 2.                                                                                       |
| VAAPI HEVC and AV1 backend           | Private FFmpeg build, exported interface, GPU import and lifetime, suspension/resumption, dynamic RC/HRD/FPS submission, level changes and reopening recovery. VBR, no B-frames and depth 1 remain. No speculative portable use of Mesa's packed quality field.                                                                    |
| AV1 geometry                         | Actual selected-device VCN4/Mesa check, 8-mod-16 firmware path, generic centred padding, neutral borders, unscaled content and resize transitions. The 1082 path has no VPP pass; other alignment cases may need one. No AV1 blacklist.                                                                                            |
| AV1 hardware/software reception      | Bounded OBU parsing, sequence/keyframe resets, timestamp-associated crop snapshots, malformed/out-of-bounds rejection, visible-frame wrapping and software plane views. Decoder prediction references keep their full allocation. Original software timestamps are restored; close paths retire contexts.                          |
| Receiver timing and packet pacing    | Reviewed native microsecond clock, wrapped hardware frame storage, Chromium reference time/remote scheduling, and scoped ALR policy default. Explicit field trials and congestion control remain native. The earlier live niri comparison is historical evidence, not a new Windows measurement.                                   |
| Bitrate and codec policy             | Default viewer-size policy remains enabled; preservation is opt-in and off by default. Its source budget updates across resizes. Optional higher maximum remains bounded and requested through reviewed application/native signaling. Camera/incoming codec behavior remains separate. Unknown JS shapes retain upstream behavior. |
| Diagnostics and settings             | Restricted native acceleration flags, no inference from decoder name alone, numeric/short-string sanitization, main-frame IPC ownership, pending/idle/error states, save rollback, source loss, keyboard and narrow/zoomed layout. The actual screensharing UI harness passes.                                                     |
| Build and update integration         | Helper input fingerprints, matching crop parser source in the owned Electron patch, staged corresponding sources/licenses, capture bundle hashes, all six native patch stages and the new 2444 scopes. Retained runtime/compiler objects were reused.                                                                              |

The Windows comparison remains in
[Windows encoder configuration and experiments](windows-encoder-parity-20261104.md).
AMF, Media Foundation, NVENC and Linux VAAPI expose different controls. Copying
an AMF number into a VAAPI property is not a reliable equivalence. Existing
hardware-specific scoping and measured low-latency settings were retained.

### Static analysis

Clang's analyzer ran over the capture, encoder wrapper, software crop, direct
Wayland, KMS, preview, Vulkan/OpenGL GPU and relay C implementations. Generated
Wayland protocol headers were supplied for the corresponding files. The fixed
capture diagnostic no longer reports its crash path.

Four remaining reports were inspected rather than patched speculatively:

- `encoder-hevc.c`: the protection array is initialized for every slot used
  after validation. Any early loop exit makes `valid` false and returns before
  that array is consumed. The analyzer loses that correlation through `&=`.
- `capture-upstream.c`: SPA's required media-type/subtype parser returns an error
  when either required property is absent. The fields are read only after
  success; they are not optional uninitialized outputs.
- `kms-client.c`: received plane counts must exactly equal the initialized FD
  inventory before assignment. All invalid inventories close their FDs and
  return without the assignment loop.
- `kms-source.c`: each frame records its owner before insertion and removes
  itself from that owner's list before destruction. Relay destruction does not
  invoke its release callback; completed callbacks do not access a destroyed
  relay afterward. The shutdown loop therefore advances the list.

These dispositions are source review plus existing sanitizer/lifecycle coverage,
not a claim that static analysis proves absence of all defects.

## Test-fixture corrections

The new high-rate delayed-acquire check exposed two problems in its synthetic
producer, independently reproduced with capture optimization disabled:

- A zero-deadline release poll needs `WAIT_FOR_SUBMIT` when the consumer has not
  yet materialized its release fence. Otherwise the kernel can return `EINVAL`
  rather than a pending timeout. See the
  [kernel syncobj contract](https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/drm_syncobj.c).
- Holding only the first unreleased dequeued buffer stopped production behind
  that lease. The fixture now searches a bounded inventory of still-owned
  buffers. It never reuses an unsignaled lease. This follows the buffer-search
  approach in [Mutter's producer](https://github.com/GNOME/mutter/blob/49.2/src/backends/meta-screen-cast-stream-src.c).

Before the corrections, 240 Hz/6 ms delayed-acquire runs stopped after eight
consumer frames and repeatedly encoded a stale picture. The corrected run
submitted 717/acquired 717/released 709 buffers, observed 716 corrected native
waits with zero errors, and delivered 353 distinct 1080p viewer frames in six
seconds. Its p99 callback interval was 20 ms. The transport validator now accepts
observed explicit-sync bypass of raw-frame pacing; it still requires the separate
explicit-sync delivery/release gates to pass.

The game benchmark also assumed a new window was tiled. This desktop's window
rules already opened the owned workload fullscreen, so blindly toggling reversed
its resize phases. Setup now checks the owned window's dimensions after moving it
before toggling. The lifecycle still requires restoration to actual 1920×1080;
the assertions were not weakened to accept an arbitrary starting resolution.

## Validation

Runtime SHA-256: `88a9e610db72e2193859316ecd58d915f54a5269857425d0fc6c89e1bb516575`.
Prepared Canary 2444 voice module:
`09c1ddd4eeb5b1583487b1806a21bf57d90bbf5e56be2dca077d8ed8f0b6ae68`.
Rebuilt helper:
`ed6ca77992aa2b4724cf6ebf7c4803e6162b660eede67563f3188a9dceb99fc6`.
Private HEVC/AV1 backend remains:
`a24c6732af75df703e5fbf2f78d8cb2d9ad95d28ac5f0191547e6cb950c0cea2`.
Rebuilt game producer:
`486377d043a7cc37f8e31fd4069dd70e75a62171a501214c3da45ca0b9fccf82`.

| Artifact                                                                    | Result and scope                                                                                                                                                                                                                                                           |
| --------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `capture-regression-final.log`, `envelope-before.log`, `envelope-after.log` | Capture ASan/UBSan regression passes; encoder contract regression fails before its production fix and passes afterward.                                                                                                                                                    |
| `game-regression-final.log`                                                 | 20 game capture tests pass, including actual C callbacks, synthetic fences and source lifetimes.                                                                                                                                                                           |
| `settings-screensharing/settings.json`                                      | Actual Preact/Electron/IPC screensharing settings check passes: defaults, saves, rollback, diagnostics, keyboard, narrow layout and zoom.                                                                                                                                  |
| `hevc-sync-final/report.json`                                               | H.265 VAAPI → Electron receiver, 1920×1080/60, 240 Hz producer with 6 ms acquire delay; capture, sync, cancellation, framing, codec and transport gates pass.                                                                                                              |
| `h264-final/report.json`, `h264-isolated/report.json`                       | H.264 hardware round trip with the verified managed OpenH264 library available; native capture, encode, decode, dimensions and transport checked. The first run overlapped the separate fullscreen workload; timing is assessed from the isolated repeat.                  |
| `av1-hardware-final/report.json`                                            | AV1 hardware round trip displays the original 1920×1080 picture through the updated receiver with no software crop calls.                                                                                                                                                  |
| `av1-software-irregular-final/report.json`                                  | 1918×1078/30 uses centred padding and the native software decoder: 178 submitted/cropped frames, no rejected/unmatched frames or live contexts at shutdown.                                                                                                                |
| `vp8-final/report.json`                                                     | VP8 software round trip at 1280×720/30 passes delivery/framing/transport. This does not establish software 1080p60 capacity.                                                                                                                                               |
| `padding-verified/report.json`                                              | 12 generated HEVC/AV1 geometry cases pass: native content detail and neutral borders across aligned, two-row and irregular sizes.                                                                                                                                          |
| `backend-lifecycle-final/report.json`                                       | Both codecs pass suspend/resume, resize, failed-update recovery, two-row↔generic padding and genuine unpadded-frame transitions, including fresh-decoder recovery.                                                                                                         |
| `game-lifecycle-verified/lifecycle-report.json`                             | Actual GLX game hooks → PipeWire → native H.264 → viewer passes every lifecycle gate after the producer fix. Initial/30/restored-60/recovered rates are approximately 59.9/30.1/58.9/59.8 unique FPS; fullscreen, tiled resize and restoration display correct dimensions. |
| `capture-final-build.log`                                                   | Complete 142-file capture bundle rebuilds, including bundled Vulkan/OpenGL hooks and corresponding source inventory.                                                                                                                                                       |

The full settings harness also ran, but stopped in an unrelated large-window
theme-editor resize assertion: Niri tiles windows and constrains the requested
size. Its X11 repeat encountered window-size policy too. These failed artifacts
are retained under `settings/` and `settings-x11/`; they are not represented as a
full-settings visual pass. The focused screensharing harness passes on the actual
desktop.

`bun run lint` and `bun run test` pass after the final production and fixture
changes: **710 Python tests, 430 JavaScript passes and 3 JavaScript skips**.
The final test log is `tests-qualified.log`; final lint is `lint-final.log`.

The freshly rebuilt helper matches the helper used in the codec matrix byte for
byte. It and the complete rebuilt capture bundle are atomically staged into both
the existing experimental sender/viewer candidate and the 2444 qualification app
under `out/discovery-csp-fix/app`. Existing native modules, runtimes and profiles
are preserved. `staged-artifacts.json` records every resulting file hash. Running
instances need a normal restart to load the updated binaries; none was restarted
or joined to a call during staging.

All tests used generated video or an owned generated game window; no account
login, messages, calls, physical camera or desktop recording was performed.

## Documentation and remaining limits

[The user guide](../screensharing.md) lists capture methods, audio/setup links,
codec availability, alignment/cropping, pacing, bitrate policy and diagnostics.
The README links it and includes Vencord's exact core warning with attribution.
It does not copy Vencord's unrelated assurances about plugins or ban history.
Writing review: approve; the defaults, actions and compatibility limits are
explicit without adding product banners or controls.

The exercised physical encoder/decoder is AMD RX 7900 GRE on Mesa/Niri. Intel and
NVIDIA paths are covered by scope/ABI/vendor tests and retained implementations,
not by physical tests here. The live niri scene-switch results from the earlier
thread remain separately qualified; this review does not establish WAN/SFU
behavior, perfect frame presentation or Windows parity on untested hardware.
HEVC still needs a compatible receiver. Proposed AV1 crop metadata is not yet a
universal standard: ordinary viewers can show padding, and some FFmpeg AV1
bitstream-filter paths reject it even though the tested native/dav1d paths
accept it. No generic CSS resolution heuristic was introduced.

The retained Electron runtime was already compiled and linked. A previous broad
`gn check` reports private Blink include visibility in the upstream Discord
integration; successful runtime compilation and these checks do not establish a
clean full-source `gn check`. No new Electron build was started for this review.

Hibernation belongs to the coordinating update thread after it receives explicit
completion and checks pending work. This review must not hibernate independently.
