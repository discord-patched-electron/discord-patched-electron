# Optimized sources in Discord's native picker, 2 November 2026

Experimental capture is now an explicit boolean preference, default off. Legacy
method preferences do not opt the user in or hijack Vanilla capture. Synchronization,
frame pacing, dimensions and encoder fixes remain installed independently.

The page-world adapter watches the semantic Go Live modal signature through the
shared webpack runtime. It does not pin module IDs, rewrite the modal source or
retrieve private presentation exports. It wraps the exported modal and immutably
extends the returned element tree: the existing tab control gets an Optimized
option first and Applications becomes Vanilla. Devices and Discord's footer,
quality/audio/preview controls retain their original callbacks and state.
Nonconfigurable webpack export getters are preserved through a proxy. Missing or
ambiguous modal/tab/native-pane contracts leave the original picker intact.

The native source pane remains mounted, hidden/inert under Optimized. Its hooks
own Discord's native picker lifecycle and consume the Update event using current
quality/audio settings. A source card changes only DPE's selected opaque token
and Discord's native source kind. The original footer callback runs after main
process validation arms that one selection; it performs Discord's release,
present and Present-store event. The native capture callback consumes the armed
source once, starts the helper, and returns its PipeWire endpoint through the
existing reviewed native bridge. Unarmed requests use Vanilla. Closing, stale
choices and startup errors retain the existing helper ownership/cancellation.

The source view is a local DPE component using the host React instance, shared
semantic styles and shared icon resolver. Its geometry follows the observed
Devices cards: two columns, 16:9 neutral icon previews, compact labels and native
selection spacing. No screen thumbnails are acquired. Screens precede detected
game hooks. The main process prefers scoped KMS after permission is enabled;
otherwise it uses supported direct Wayland outputs. Unavailable sources cannot
be armed, source tokens belong to one renderer, and catalog refresh keeps token
identity stable for selection/focus. Game hooks are listed without starting a
pixel producer. Discovery stops polling when Optimized is not visible.

Only the settings window exposes the persistent permission action. The picker
cannot install rules or request interactive privilege. See
[the scoped permission design](kms-scoped-permission-20261102.md). Experimental
mode requests the native picker path only when Discord reports actual support;
it never invents that capability. Vanilla then uses the ordinary native picker.

Public source evidence is retained in `out/optimized-picker-research/`. The current
modal contract was inspected in public asset `72ee4de515e27366.js`; these asset
names are evidence, not runtime pins. All checks were deferred until implementation
was complete, as requested. Final validation results are recorded below.

## Final validation

- `bun run lint` and `git diff --check` passed.
- The full Python suite passed all 651 tests. The first JavaScript run exposed
  two browserless settings fixtures; an early environment guard fixed these,
  then the complete JavaScript suite passed all 367 tests.
- Six focused native/permission tests passed, including sanitizer-backed KMS
  startup, cancellation and mocked scoped authorization. The KMS helper rebuilt
  successfully; Electron was not rebuilt.
- The real settings harness passed experimental off/on, authorization gating,
  cancellation/retry/granted states, keyboard use, narrow layout, zoom and themes.
  Evidence: `/mnt/storage/dpe-optimized-picker-20261102/settings/settings.json`.
- A fresh signed-out shell loaded the current public Canary modal and real
  React 19.2.3. Its returned tree retained the original state, native source pane
  and footer, with Optimized, Vanilla and Devices tabs in order. This verifies
  the current modal contract, not a live share. The harness could not locate a
  ReactDOM export for its optional DOM assertion; that assertion did not run.
  Evidence: `out/optimized-picker-final/native-shell/result.json`.
- A separate isolated DOM fixture rendered the actual source-card component and
  shared styles, and passed keyboard/selection assertions. Its screenshot was
  inspected. This fixture uses Preact and synthetic sources; it does not establish
  exact visual parity for the complete live Discord dialog.
  Evidence: `out/optimized-picker-final/cards.log` and `cards.png`.

The existing test build at
`/mnt/storage/dpe-kms-20261102/candidate/launch-test.sh` contains the new settings,
picker adapter, helper and scoped permission installer. Its capture manifest and
distribution validate, and the Electron binary is unchanged. Restart this build
to load the frontend changes. No system permission rules were installed, no
authentication dialog was exercised and no account/call was used for validation.
The Allow action and a live outgoing share therefore remain unverified end to end.
