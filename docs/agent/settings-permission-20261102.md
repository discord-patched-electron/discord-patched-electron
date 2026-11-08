# Settings permission implementation — 2 November 2026

Coordination contract for the native picker and settings:

- `capture-permission.cjs.status()` is read-only. `configured` means a trusted
  per-user v1/v2 marker exists, even if obsolete or blocked. Use this for the
  settings checkbox and installed/uninstalled text.
- `allowed` remains the sole permission gate for usable direct/KMS capture.
  Picker sources must not treat `configured` as authorization.
- `needsUpdate` identifies obsolete/incomplete helper installation. Current
  rules denied by polkit report `permission-denied`. `canRevoke` describes
  whether this process can run the removal flow.
- Settings never saves experimental capture off after cancelled/failed required
  revocation. Explicit enable may migrate an existing rule through authorization.
  Status reads never install, migrate, or revoke anything.
- The picker’s Learn More destination is
  `https://github.com/discord-patched-electron/discord-patched-electron/blob/main/docs/optimized-screensharing.md`.

## Validation

Artifacts: `/mnt/storage/dpe-settings-review-20261102/`.

- `settings-03/settings.json`: real settings harness passed on the supplied
  Electron 46 runtime, fresh profile and private Xvfb display. Covers clipboard,
  installed legacy/blocked grants, removal confirmation and authorization
  cancellation, successful removal before disabling, explicit-enable migration,
  keyboard, theme, narrow/zoomed layout, reduced motion and accessible names.
- `lint-final.log`: `bun run lint` passed.
- `test-final.log`: `bun run test` passed, 661 Python and 388 JavaScript tests.
- `relevant.log`: 41 permission, presentation, screensharing and codec tests passed.
  With `DPE_CODEC_RENDERER_SOURCE=out/optimized-picker-research/web.d01f79ca6dd8c64a.js`,
  the codec suite executes the retained Discord client's actual capability-list
  assignments and native encoder-fallback body. Synthetic capabilities check
  encode-only exclusions, unchanged decode/payloads and camera/viewer isolation.
- `settings-full/settings.json`: the optional broader settings harness stopped
  at its pre-existing window-size assertion (945×1018 instead of 1100×800), before
  the flags page. The requested screensharing scope passed independently.
- Screenshots are private synthetic settings windows. The removal dialog was
  visually inspected. These checks do not establish exact upstream visual parity.

No account login/call, physical camera, root authorization, Electron rebuild or
candidate staging occurred. Permission IPC is mocked in the UI harness. Native
source tests are not proof of live SFU negotiation or hardware encoding.

## Files owned by this implementation

- `integration/updater/ui/screensharing.jsx`
- `integration/updater/ui/primitives.jsx`
- `integration/updater/config-preload.cjs`
- `integration/updater/presentation.cjs`
- `integration/updater/capture-permission.cjs`
- `integration/discord-ui/components.css`: only appended shared confirmation and
  disabled-content rules, outside the capture-picker section.
- `tests/launch-settings/screensharing.cjs`
- `tests/updater/capture-permission.test.cjs`
- `tests/updater/presentation.test.cjs`
- `tests/updater/video-codecs.test.cjs`
- `docs/optimized-screensharing.md`
- `docs/linux-launcher.md`
- `docs/agent/settings-permission-20261102.md`
- `docs/agent/README.md`: index entry.

Existing dirty work was preserved. Production codec behavior and the permission
installer did not need changes. No capture-picker, preview-backend or badge files
were edited by this implementation.
