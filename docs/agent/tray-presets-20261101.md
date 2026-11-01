# Bundled tray presets, 1 November 2026

Later UI and per-state selection changes are recorded in
[individual tray presets](tray-row-presets-20261101.md). The results below describe
the original shared-preset implementation.

Appearance's existing Tray Icons disclosure now has the shared Discord replica
Select for Discord default, Vencord and Equicord. It autosaves and refreshes the
native tray. Existing per-state custom PNGs remain overrides; Reset restores the
selected preset for that state. Row subtitles name the actual image source, with
temporary update/error feedback retained. Preset save errors appear immediately
below the selector without adding a success banner or descriptive subtitle.

`integration/updater/assets/tray-presets/manifest.json` records the original
upstream image hashes, revisions, paths, archive hashes and complete GPL license.
Vesktop supplies default/unread images only; its default is reused for its voice
states. Equibop supplies all six matching states. Original image files remain
unchanged. Electron decodes and scales bundled images to 32px, then caches them
by preset/path; neither settings reads nor tray-state events fetch the network.
Invalid saved presets and missing images fall back to Discord's originals.

Project links includes Attributions, using the existing allowlisted external
link flow to the repository's `THIRD_PARTY.md`. That file and the preset license
also ship in the local package. As with other repository links, the public page
reflects the default branch when these changes are published.

Unit coverage checks live switching, image caching, override retention, reset,
failed saves, invalid names, window ownership and original asset/license hashes.
The native settings harness checks real image decoding, dropdown interaction,
autosave, tab remount, source subtitles and the keyboard-operated credits link.
Evidence and the updated test app live under
`/mnt/storage/dpe-tray-presets-20261101/`.

Final verification: lint and all 747 unit tests pass (506 Python, 241 JavaScript).
`settings-final/settings.json` passes all 39 native settings groups. Screenshots
of both presets were inspected after removing redundant spacing above the shared
Select. `launch-retry/launch-report.json` passes fresh onboarding, the login page,
codec preload and two native popout checks; the first launch check reached login
but timed out before recording codec/popout verification. No account was used.
All packaged preset images, notices and integration sources match the checkout.
Run `/mnt/storage/dpe-tray-presets-20261101/test.sh`; it reuses the earlier isolated
manual profile and forces the verified bundled runtime.

## better-ui review

No actionable UI-polish findings in the inspected states. This change reuses
the existing Select and project menu, including selected checks, keyboard focus,
disabled styling and reduced-motion behavior. Transparent logo art has no added
outline. Native screenshots cover both presets; the existing harness exercises
narrow layout, zoom and tokens. The actual system tray on the user's compositor
is not verified by this settings harness. Approve for the inspected states.
