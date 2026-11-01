# Desktop controls and Linux mouse preferences

This records the initial implementation. Later [settings refinements](settings-refinements-20261101.md)
remove the browser WebRTC option and extend control visibility to DPE and native
auxiliary windows.

Settings reuse the Discord replica Switch, Select and section typography.
Appearance groups native titlebar, Discord's three existing window buttons and
minimum-size enforcement. General holds startup, tray, notifications and browser
WebRTC policy. Flags has a Scrolling & mouse section with ordinary switches,
autosave and the existing next-launch/Undo behavior. Plain switches do not render
empty description elements; dependent controls are disabled and muted.

Flags previously captured the current Discord System sidebar icon, now a
keyboard. The presentation bridge now resolves only the named flag export and
uses a new semantic key, retiring older cached System drawings while retaining
the existing flag fallback.

## Native integration

`integration/desktop/preferences.cjs` owns normalized defaults and tracked native
windows. Its paired source transform inserts validated calls at existing desktop
integration points. It leaves client archives and renderer application code
unchanged and does not replace Electron prototypes. Window-button customization
only hides existing Discord controls with scoped CSS; OS window-manager actions
and unrelated toolbar/activity controls remain available.

The current public Canary renderer's window component was inspected in an
isolated logged-out session. Its scoped `winButtons` container renders minimize,
maximize and close in that order. The source URL, digest and relevant component
are recorded in
`/mnt/storage/dpe-desktop-options-research/source-probe/window-controls-evidence.json`.
The older `titleBar` container uses the reverse button order; both layouts have
scoped selectors and native coverage. All 15 source transform sites match the
actual locked Canary 1.0.2357 and retained 1.0.2422 bundles. Their differing popout
setup signatures are accepted explicitly; ambiguous layouts still fail validation.

Desktop saves send a validated key/value patch and merge against the current
stored configuration. Stale General/Appearance snapshots cannot overwrite the
desktop namespace. Persistence failures restore the prior configuration before
applying anything. A failed live native update retains the saved values and asks
for a restart rather than falsely reverting the UI to unsaved values.

Native titlebar requires a new BrowserWindow; pending changes are shown in the
sidebar. Individual button choices remain stored when the native frame forces
them off. Minimum size, tray, badges, flashing, button CSS and WebRTC policy update
live. Turning off tray gates the existing close-to-tray path and restores hidden
main windows to the taskbar. Start minimized applies on normal and OS-autostart
launches; it does not itself enable OS autostart.

The WebRTC values match Electron's native policies, also exposed by
[Equibop](https://github.com/Equicord/Equibop/blob/main/src/shared/settings.d.ts).
[Electron's policy API](https://www.electronjs.org/docs/latest/api/web-contents#contentssetwebrtciphandlingpolicypolicy)
controls browser WebRTC. Discord's native voice implementation is outside this
API, which is explicitly stated beside the selector.

## Middle-click paste

The unmodified runtime accepts
`--blink-settings=middleClickPasteAllowed=false`, but subsequent renderer
preference updates (including a WebRTC policy update) restore the system value.
That makes the switch alone insufficient.

`patches/ports/46/middle-click-paste.patch` preserves that explicit setting after
Linux renderer preference updates. It reuses Blink's existing command-line
parser for this single setting. Paste is suppressed at the native primary-paste
decision; no mouse events are canceled and no DOM listeners or clipboard-clearing
workarounds are added. The maintained follow-up port and locked candidate tree
include the native fix. Other Blink settings retain their original behavior.

The flag resolver treats Blink settings as named members, preserving unrelated
overrides while toggling paste. Native autoscroll uses `MiddleClickAutoscroll`;
smooth scrolling uses `--disable-smooth-scrolling`. Recovery mode and flag Undo
continue to apply to all three settings.

## Verification

The retained Electron checkout was rebuilt incrementally with two Ninja jobs;
the existing checkout and compiled objects were retained. The verified artifact
is `/mnt/storage/dpe-middle-click-20261101/artifact`. Its capacity report records
the updated source identity and runtime archive hash.

Native mouse tests are in `tests/middle-click`; native window/CSS tests are in
`tests/desktop-preferences`. The settings harness exercises dependent controls,
autosave, selector behavior, retired icon caches, input flags, keyboard use,
small windows, RTL, zoom and live theme tokens. Evidence is under
`/mnt/storage/dpe-desktop-options-20261101/`, with native mouse evidence under
`/mnt/storage/dpe-middle-click-20261101/`.

Final lint and all 744 unit tests pass (506 Python, 238 JavaScript). The real
settings harness passes all 37 check groups on the rebuilt runtime, including
the earlier spelling, tray and flags behavior. Native X11/Openbox verification
confirms both window-control layouts, preserved unrelated controls, minimum-size
restoration, all four WebRTC policies, actual native frame decorations and
minimized windows when tray is disabled. Its report is at
`/mnt/storage/dpe-desktop-options-research/native-x11-openbox/result.json`.

The portable test app is staged at
`/mnt/storage/dpe-desktop-options-20261101/app-verified`. Run
`/mnt/storage/dpe-desktop-options-20261101/test.sh` to reuse the earlier isolated
manual test profile with the new bundled runtime. `TESTING.txt` beside it lists
the settings and a fresh-profile override. No user account was accessed.
The packaged app passed first-run onboarding, the real Discord login page and
two popout handshakes against the locked client, using a fresh profile. The
report is `launch-verified/launch-report.json` in the same evidence directory; the
staged build is marked `launch_verified: true` and its runtime digest matches
the native input/settings test runtime. This is a local test build, not a full
media-qualified release.

### better-ui review

| Severity | Location                                  | Before                                              | After                                                 | Why                                              |
| -------- | ----------------------------------------- | --------------------------------------------------- | ----------------------------------------------------- | ------------------------------------------------ |
| MEDIUM   | `integration/discord-ui/presentation.cjs` | System/keyboard drawing reused for Flags            | Named flag lookup with retired cache key              | Icon meaning matches the action                  |
| MEDIUM   | `integration/updater/ui/primitives.jsx`   | Description-free switches still emitted empty hints | Conditional descriptions and shared controlled Select | Preserve native spacing and avoid redundant copy |

Native screenshots inspect Window, Flags and General controls. The existing
token and keyboard checks remain in the real settings harness. Screen-reader
output and signed-in notification/tray transitions are not verified. Approve
for the inspected UI states; no high-severity UI-polish finding remains.
