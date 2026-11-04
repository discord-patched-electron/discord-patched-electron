# Persistent Optimized picker previews, 2 November 2026

This pass supersedes the one-shot/one-Hz preview design in
[picker refinements](picker-refinements-20261102.md). It extends the existing native
modal; it does not replace Discord's picker or change share encoding.

## Picker and permission

Both KMS and direct Wayland monitors are absent when `permission().allowed` is
false. Cached monitor selections and previews recheck that permission. The legacy
direct Wayland request/choose path also checks it, so it cannot bypass the option.
Permission status reconciliation remains owned by `capture-permission.cjs`.

When no available optimized source exists, the Vanilla shortcut cannot keep the
grid alive. A centered screen icon, the requested empty-state message, explanation
and Learn More link point to the Screensharing guide. The tab bolt inherits the
native icon sizing contract without applying the shared settings root's sizing.
Images are absolutely positioned inside fixed 16:9 cards and cropped centrally;
portrait game frames cannot stretch a card. The native cog and its returned menu
receive small semantic wrappers. A native menu item immediately before
`advanced-items` opens Screensharing in the separate DPE window. The restricted
IPC validates the trusted main frame and accepts no destination, path or window
options from the page. Existing native menu entries remain intact.

## Sessions and bounds

Visible cards request independent frames on a 30-fps ceiling (34-ms browser timer).
There are at most four live source sessions globally, one outstanding frame per
source, and no queued thumbnail frames. Rendering bounds times devicePixelRatio
are fitted to at most 480×270. A slow source cannot block the other cards.

Monitor sessions retain the compositor/KMS connection, GPU context and thumbnail
render target. Each frame is GPU-downscaled before the small BGRA readback.
Game sessions retain one hook socket and GPU import per source, with a bounded
broker dictionary instead of its former single preview job. The bundled hook's
exact generation/sequence lease is released only after synchronous thumbnail
readback completes. This is essential: keeping the socket without releasing the
first frame would freeze subsequent previews. Stock hooks use their existing
implicit-sync texture sampling contract. Both paths reuse the thumbnail target
and reset requested dimensions per frame when a source changes aspect ratio.

The next frame requires a one-byte demand from the parent. No PipeWire stream or
video encoder is created for previews; Electron converts each delivered thumbnail
to JPEG for the card. GPU readback and JPEG conversion still cost work and the
native compositor may require a full-size source GPU buffer. This is a bounded
preview path, not a claim that capture itself operates at thumbnail resolution.

Closing the picker, changing tabs, hiding the document, starting capture or
owner destruction cancels sessions. Invisible/unrequested sessions expire after
750 ms in main, one second in the game broker; native alarms bound wedged helpers.
Permission is rechecked at most once per second per monitor session, avoiding a
polkit subprocess per frame. Failure retries back off for two seconds. Preview
requests refuse acquisition while a DPE share is starting or active, and never
stop an active producer. A real share still receives a fresh hook handshake after
preview cancellation. No captured pixels are written to files or logs.

## Upstream socket limitation

Primary source at the pinned upstream commit
`8a2c43295fae2ede8ffd382a1581281ceb6153c0` confirms the boundary:

- [`capture_try_connect` in capture.c](https://github.com/nowrep/obs-vkcapture/blob/8a2c43295fae2ede8ffd382a1581281ceb6153c0/src/capture.c#L87-L123)
  connects each hook to one fixed abstract Unix socket and stores one connection.
- [`server_thread_run` in vkcapture.c](https://github.com/nowrep/obs-vkcapture/blob/8a2c43295fae2ede8ffd382a1581281ceb6153c0/src/vkcapture.c#L913-L942)
  binds that endpoint and returns when binding fails. Its client array accepts
  multiple game processes; `listen(..., 1)` is a backlog, not a one-game limit.

Consequently OBS and DPE cannot simultaneously own the stock endpoint in the
same network namespace. DPE retains conditional hiding of stock sources and a
transition-only privacy-preserving log when another owner occupies it. This is
not an unconditional limitation on all game capture: bundled DPE hooks use a
separate endpoint, and separate game sources can be previewed concurrently.
Stock hooks do not acquire DPE's explicit frame-release or producer-pacing
capabilities simply because the receiver accepts their protocol.

## Validation and remaining limits

Artifacts are under `out/picker-refinement/persistent/`:

- `monitor/dpe-screen-preview` and `game/dpe-vkcapture-preview` were built with
  warnings as errors. `game/dpe-vkcapture-source` is also rebuilt by that helper
  build script; no Electron rebuild or candidate staging was performed.
- The current public Canary native-shell fixture uses a fresh isolated profile
  and React 19.2.3. `native-shell/result.json` confirms original state, source pane,
  footer, tabs and cog remain, with exactly one settings item immediately above
  Advanced and one Refresh action. No account login or call was performed.
- `cards.log`, `cards.png` and `empty.png` are isolated **synthetic** UI evidence:
  portrait JPEG cards stay 16:9 at 100%/150% zoom, keyboard selection/focus works,
  long names truncate, requested pixel dimensions are bounded, and a lone Vanilla
  shortcut yields the true empty state. Images were inspected. This does not
  establish exact visual parity with every live Discord theme/window size.
- `game-obs-result.json` and `game-dpe-result.json` each validate 60 nonblank frames
  for each of two synthetic game processes, stable catalog tokens and cleanup.
  Both paths measured about 28.3 frames/s per source after warmup at 160×90.
  Startup lowers the stock-hook whole-run average to about 19.8 frames/s. This
  supports concurrent near-30-fps previews, not a guaranteed 30 fps on all GPUs,
  source counts, resolutions or renderers. Only dimensions/counts/timing are saved.
- `bun run lint` passed. `bun run test` passed: 661 Python tests and 388
  JavaScript tests (387 passed, one skipped). The final icon sizing adjustment
  also passed the focused picker suite.
- Controller tests cover bounded concurrency, retained session identity, duplicate
  demand rejection, ownership, permission revocation, close/start cancellation
  and suppression during active shares. A subprocess stream test verifies two
  frames from one process and no second outstanding demand. Broker tests verify
  reuse and release before reconnecting for actual capture. UI tests cover the
  native menu insertion, original actions, empty state and source selection.

No live physical screen or root-authorized KMS capture was run for this pass.
Monitor persistence is compiled and exercised by synthetic IPC/protocol tests;
compositor reconfiguration retires the session and retries rather than attempting
to reuse buffers from an obsolete generation. More than four visible sources
retain placeholders until a slot becomes available. Actual end-to-end browser
preview FPS and behavior during a logged-in Discord share remain unmeasured.

## Files owned by this implementation pass

These are the exact edited repository files; the shared checkout contains other
agents' unrelated changes. Nothing was staged or committed.

- `integration/discord-ui/capture-picker.cjs`
- `integration/discord-ui/capture-picker-view.cjs`
- `integration/discord-ui/components.css` (capture card/empty-state rules only)
- `integration/updater/capture-backends.cjs`
- `integration/updater/capture-preview.cjs`
- `integration/video/capture-backends.cjs`
- `integration/video/screen-preview.c`
- `integration/video/vkcapture-preview.c`
- `integration/video/vkcapture-broker.py`
- `integration/video/kms-gpu.c`
- `integration/video/vkcapture-gpu.c`
- `tests/discord-ui/capture-picker.test.cjs`
- `tests/updater/capture-backends.test.cjs`
- `tests/updater/capture-preview.test.cjs`
- `tests/test_vkcapture_backend.py`
- `docs/linux-launcher.md` (permission/stock-endpoint sentences only)
- `docs/agent/README.md` (this page's index entry only)
- `docs/agent/picker-refinements-20261102.md` (historical-preview notice only)
- `docs/agent/picker-persistent-previews-20261102.md`

The parent should stage the current sidecars/UI and rebuilt native helpers using
the existing packaging flow, then regenerate/verify the candidate manifest. The
retained `/mnt/storage/dpe-kms-20261102/candidate/launch-test.sh` was not launched,
changed or used for profile/account copying. Isolated Electron harnesses reused
verified runtime files with separate synthetic apps and fresh profiles.
