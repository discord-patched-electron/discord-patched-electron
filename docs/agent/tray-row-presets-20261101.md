# Individual tray presets, 1 November 2026

Each tray-state row now offers Discord, Vencord and Equicord as inline actions.
They use the shared inline-link treatment and an `InlineBullet` replica matching
the screen-share audio dialog's aria-hidden bullet and semantic spacing. The
current preset is underlined and exposed through `aria-pressed`; a custom image
leaves all three unselected. Transient picker/reset feedback still occupies the
row's second line. Selecting a preset does not add a success message.

`trayIconPreset` remains the baseline. `trayIconPresets` stores individual state
overrides, while `trayIcons` retains custom PNGs. Selecting a row preset validates
both identifiers, removes only that state's custom PNG reference and saves both
changes atomically. Reset clears both overrides for the row. Main-preset changes
preserve row overrides, and older configurations need no migration. Bundled
images continue to use the existing decoded-image cache and stock fallback.
The new IPC method is restricted to its owning settings window.

The essential-flags OpenH264 license/instructions paragraph was removed, and
the Variables label now has a literal separating space before the first code
element. Packaged codec notices remain intact.

Evidence and the prepared test build live under
`/mnt/storage/dpe-tray-row-presets-20261101/`. Run `test.sh` there; it reuses the
earlier isolated manual profile with the verified native runtime.

## UI review and validation

The tray rows let their action buttons wrap below the label at narrow widths.
Native screenshots also exposed an existing 8px overflow from the project
header's artwork: the mobile sidebar used 8px padding while the artwork retained
its 16px negative inset. The mobile inset now matches its sidebar padding.

| Severity | Location                                  | Before                                            | After                                         | Why                                                        |
| -------- | ----------------------------------------- | ------------------------------------------------- | --------------------------------------------- | ---------------------------------------------------------- |
| Medium   | `integration/updater/launch-settings.css` | Header artwork extended beyond the narrow layout. | Its mobile inset matches the sidebar padding. | Consistent surface geometry prevents horizontal scrolling. |

The initial 40-group native settings run passed. Keyboard verification then
required the full Electron key-down/character/key-up sequence for native button
activation; no application keyboard handler was added. The subsequent run passed
keyboard selection, focus retention and 320px/720px tray-row checks. The final
run additionally checks the scroll container itself for horizontal overflow.

`bun run test` passes 757 tests (509 Python, 248 JavaScript), including per-state
artwork/caching, replacement of one custom image, baseline changes, reset,
invalid values, rollback and IPC ownership. The packaged app uses the previously
verified native v2 runtime; its executable SHA-256 is
`5a36b7c2d18848a32442b7a7b2b28ec7f9361c55a81292a7f757f70424cf99e8`.

Final verification: lint passes; `settings-verified/settings.json` passes all
40 groups, including native keyboard activation and no horizontal overflow at
320px/720px. The normal, narrow, mixed-preset and essential-flags screenshots were
inspected. `launch-final/launch-report.json` passes fresh onboarding, the real
login page and both popout handshakes. `app-final/` contains the matching source
files and UI bundle, and `test.sh` launches that build.

No actionable UI-polish findings remain in the inspected states. The physical
desktop tray and signed-in calls were not exercised. Approve for the inspected
settings states.
