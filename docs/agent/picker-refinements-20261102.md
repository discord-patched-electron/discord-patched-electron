# Picker refinements and self-contained capture, 2 November 2026

This pass follows user feedback on the first Optimized picker. It preserves
Discord's modal, tabs, native source lifecycle, quality/audio controls and footer.
The source cards now use a selected outline and hover Select overlay, without a
checkbox. Refresh is placed beside the existing settings cog. If its semantic
contract changes, Refresh remains beside the native footer action instead of
guessing a private component. The last tab is stored as one bounded enum in the
client's local storage; Devices is restored through Discord's tab callback.

An Other window (Vanilla) card follows the game sources. Selecting it sets the
native window kind, and the original footer opens the ordinary system picker
without arming any experimental source. Vanilla retains the independent
synchronization, capture pacing, dimensions and encoder corrections.

The fallback image is the native Devices thumbhash
`GQgGHISKZ5aYqYeYhX9isDUHGw`, observed in the public Canary source inventory.
It was decoded once with the upstream MIT-licensed
[ThumbHash implementation](https://github.com/evanw/thumbhash/blob/main/js/thumbhash.js)
and embedded as a small PNG in the shared styles. It contains no camera or screen
content. No private Discord presentation component is imported to render it.

## Preview lifetime and resource limits

Historical design: superseded by [persistent previews](picker-persistent-previews-20261102.md),
which retain bounded sessions and allow concurrent sources.

Only visible cards acquire previews. Each request measures the card in CSS pixels
and multiplies by device pixel ratio (including browser zoom), then fits at most
480 by 270 pixels. Requests are serialized with at least one second between
completions, rotating visible sources. Main process enforces ownership, current
catalog tokens, bounds and its own rate limit. It permits only one preview job
globally and refuses acquisition while a DPE share is starting or active.

Monitor previews use a separate one-shot helper: direct Wayland capture into a
GPU buffer, or the narrowly authorized KMS framebuffer import, followed by GPU
downscaling and readback of only the thumbnail. There is no PipeWire stream or
encoder in this path. The direct compositor protocol still requires a full-size
GPU capture buffer; low thumbnail resolution does not change that requirement.
Aspect ratio is preserved. Parent death, a three-second native alarm and a
four-second main-process limit bound failed captures.

Game previews briefly borrow an idle hook, GPU-scale one frame and release it.
They never interrupt an active producer. BGRA thumbnail bytes travel only through
bounded helper IPC and are encoded to JPEG in main process. No pixels are saved
to disk or included in diagnostics. Closing/switching away from Optimized cancels
preview work and releases idle broker discovery. Arming a source cancels previews
before the original native capture action runs. Missing, busy, incompatible and
failed previews retain the same Devices gradient.

## Permission, compatibility and settings

The reported authorization failure was the follow-up check, not a rejected
installer: local journal evidence showed successful installation. Non-root
`pkcheck` cannot supply arbitrary action details. The replacement uses a dedicated
polkit action bound to the installed DPE server and queries that action without
details. The server is bundled with corresponding source; no GPU Screen Recorder
installation is needed. See [the scoped permission design](kms-scoped-permission-20261102.md).

The settings action is now an asynchronous shared Switch. It is disabled during
authentication, returns to confirmed state on failure, and supports revocation.
The permission validates dynamic DRM primary devices instead of pinning monitor,
connector or GPU IDs. Its active/local account restriction remains in effect.

Upstream OBS vkcapture hooks are accepted using their unchanged protocol. Their
single abstract socket cannot be owned by OBS and DPE simultaneously, and their
implicit synchronization does not provide the bundled DPE hooks' producer pacing
or explicit release protocol. Compatibility capture reports that distinction
instead of calling it direct buffer sharing. The stable per-user game wrapper
resolves the current application on each launch, independent of version paths.

Screensharing settings consolidate the screen audio options and add outgoing
codec exclusions. Exclusions are applied to our outgoing stream's advertised
encoder capabilities and native fallback lists; camera and decoding capabilities
are retained. Excluded legacy codecs are not silently restored if no permitted
hardware encoder exists.

## Validation

Checks were deferred until implementation was ready, as requested.

- Final `bun run lint` and `bun run test` passed: 661 Python tests and 376
  JavaScript tests. The first pass caught a missing standalone packaging import
  and stale assertions referring to the removed GSR dependency; these were fixed.
  Pinned exporter source is excluded from formatting and verified by checksum.
- The real Screensharing harness passed permission on/off/pending/cancel/retry,
  codec menu and persistence, keyboard, zoom, narrow layout and theme cases.
  Evidence: `/mnt/storage/dpe-picker-refinement-20261102/settings/settings.json`.
- The current public Canary modal with React 19.2.3 retained its original state,
  source pane and footer. The native cog stayed in place and exactly one Refresh
  control was added. The separate card DOM fixture passed selection, keyboard
  and preview-bound checks. Its synthetic screenshot and settings screenshots
  were inspected. These checks do not constitute a logged-in live-call test.
- Direct Wayland thumbnail acquisition passed on this machine at 320×180 BGRA.
  Only dimensions and byte count were retained, not pixels. A new synthetic GL
  window using unmodified upstream vkcapture passed thumbnail acquisition,
  reconnection and actual native encoder/decoder delivery. See
  [the game compatibility results](game-compatibility-20261102.md).
- Eight focused KMS permission/native fixtures passed, including ASan/UBSan
  protocol and descriptor checks, and three permission IPC tests passed. The
  standalone server and producer compiled. No agent-installed grant or actual
  root authorization was used: the user must enable the updated toggle to replace
  their previous version-one grant. Live KMS capture with that new grant remains
  unverified until that action.
- A single bounded twelve-second 1080p60 native game-capture check used a
  4,000-FPS source cap and GPU pressure, AMD H.264 and an Electron decoder. Every
  capture, encoding, delivery, timing and pressure validation gate passed. This
  did not reproduce sustained bitrate collapse, and it is not a guarantee about
  all games, actual Minecraft FPS or Discord SFU/viewer/network behavior. No new
  bitrate floor or disabled congestion control was introduced.

Final artifacts are under `out/picker-refinement/`; the high-FPS report is
`/mnt/storage/dpe-picker-refinement-20261102/bitrate-high-fps/native/report.json`.
The updated candidate remains
`/mnt/storage/dpe-kms-20261102/candidate/launch-test.sh`. It includes independently
built capture/preview helpers, current UI/sidecars and corresponding source, with
a validated manifest and unchanged Electron executable. Restart it to load the
changes and register the stable per-user game wrapper. No account was copied,
logged in or used for calls; no captured content was saved.
