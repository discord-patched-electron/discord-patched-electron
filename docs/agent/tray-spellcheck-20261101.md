# Tray icons and Linux spelling

The separate DPE Settings window now provides per-state PNG tray icons in
Appearance and simultaneous spelling languages in Spelling. Neither feature adds
UI inside Discord. Tray images are decoded, bounded, resized and copied into the
profile, then selected through Discord's existing tray state function. Missing
or damaged images fall back to Discord's originals.

Spelling uses Electron's Chromium dictionary catalog, downloads, cache,
suggestions and personal dictionary. Two main-process language setters are
adapted so Discord's UI locale or detected language cannot discard the user's
bilingual selection. Existing spellcheck result, replacement and learned-word
IPC remain intact. The session setting applies immediately; system languages
are the default. Dictionary download failures are visible in settings.

References:

- [Electron spell checker](https://www.electronjs.org/docs/latest/tutorial/spellchecker)
- [Equibop tray implementation](https://github.com/Equicord/Equibop/blob/main/src/main/tray.ts)

## Validation

Local evidence is in `/mnt/storage/dpe-tray-spellcheck-20261101/`:

- `lint.log`: repository lint passed.
- `tests-final.log`: 505 Python and 211 JavaScript tests passed.
- `settings-final/settings.json`: 31 real Electron settings groups passed,
  including bilingual persistence, empty-selection validation, tray controls,
  keyboard behavior, narrow windows and zoom.
- `spellcheck/native/result.json` and `repeat.json`: actual English and French
  dictionaries accepted both languages, flagged a typo, provided suggestions,
  replaced the word, toggled checking and retained a personal word across runs.
- `tray/smoke.log`: native PNG decoding, resizing, import and reload passed.
- `launch/launch-report.json`: fresh setup and signed-out Discord launch passed;
  `launch/relaunch/` records the repeated launch.

The test build reuses the approved Electron 46.0.0-alpha.1 archive with SHA-256
`f70c12b4c543773964107c4c77c1ee121d81b3d723e60ea84fac114110ee498a`.
Run `/mnt/storage/dpe-tray-spellcheck-20261101/test.sh` for the isolated manual
profile; `TESTING.txt` beside it describes both feature checks.

No unattended account login, message or call was performed. The logged-in
composer and visible tray state transitions still require manual verification.
Screen-reader output was not verified; labels and keyboard behavior were checked.

## Settings refinement

Spelling now has its own tab and uses the shared controlled Switch component for
all toggles. Its searchable catalog combines language selection and dictionary
status, with an enabled-only filter and a compact refresh action. System mode
disables manual controls. Saves apply immediately; failed saves restore the last
accepted selection. The download checkmark comes from Chromium initialization or
a cached BDic file, not merely from membership in the supported-language catalog.

Flags now serialize debounced autosaves with revision checks. Newer edits survive
an in-flight write; invalid drafts keep the last saved file unchanged. Closing
during a valid pending save waits for completion. The sidebar's Undo restores the
configuration from when the window opened. Turning off essential editing restores
essential defaults and saves them. The Save button is removed.

Flags and Tray icons share one Disclosure component. Appearance scrolls with a
fixed-height CSS editor. `AGENTS.md` now documents the required replica components,
live Discord tokens and verification conventions for future UI work.

### better-ui review

| Severity | Location                                  | Before                                            | After                                                | Why                                                                |
| -------- | ----------------------------------------- | ------------------------------------------------- | ---------------------------------------------------- | ------------------------------------------------------------------ |
| MEDIUM   | `integration/updater/ui/desktop.jsx`      | Browser checkboxes and detached dictionary status | Shared switches and unified searchable language rows | Match the established control system and make state understandable |
| MEDIUM   | `integration/updater/ui/primitives.jsx`   | Tray disclosure had separate styling              | Flags and Tray share Disclosure                      | Use one implementation of the measured Discord surface             |
| MEDIUM   | `integration/updater/launch-settings.css` | Expanding Tray reduced CSS editor space           | Fixed-height editor in the main scroller             | Keep the editor usable without changing its size during expansion  |

Refinement evidence and the rebuilt app are under
`/mnt/storage/dpe-settings-refinement-20261101/`. The native harness covers
autosave/Undo, pending-save closure, bilingual selection, filtering, disabled
system mode, fixed editor height and existing keyboard/zoom/token checks.
Screen-reader output and a new live signed-in Discord comparison are not verified.

The final refinement suite passed 505 Python tests, 213 JavaScript tests and 33
real Electron settings groups. Screenshots and switch geometry evidence confirm
the manual/system states, readable language names and the fixed CSS editor.
Linux's `c` locale is filtered before passing preferences to `Intl.DisplayNames`;
otherwise it invalidates the entire locale list and leaves raw language codes.

Run `/mnt/storage/dpe-settings-refinement-20261101/test.sh` for the new build. It
reuses the earlier isolated manual test profile; `TESTING.txt` documents a fresh
profile override. No hibernation is scheduled for this refinement.

Approve for the inspected UI states; no high-severity UI-polish findings remain.

## Header and control follow-up

The CSS editor now has a 320px minimum and grows with its available space rather
than staying fixed-height. Spelling starts directly with its switches, uses the
existing section-heading typography for Languages, and mutes disabled labels and
descriptions. The enabled-only filter has a primary selected state and checkmark.
Tray icons has no summary description; its label changes between Tray Icons and
Hide Tray Icons through the shared Disclosure component.

The project header reuses onboarding artwork and its masked progressive blur
layers, fading into the current sidebar color. Decorative layers cannot intercept
input and are hidden for reduced transparency or forced colors. Both light and
dark settings were visually inspected.

Evidence: `/mnt/storage/dpe-settings-polish-20261101/`. The native settings tests
cover editor growth and minimum height, disclosure labels, disabled text,
selected filter appearance, loaded header artwork and existing interaction checks.
Launch `/mnt/storage/dpe-settings-polish-20261101/test.sh` to use the updated build
with the previous isolated manual profile. No hibernation is scheduled.

## Language row spacing and empty selection

The Languages heading now leads directly to the search controls with one 24px
heading gap. Language metadata uses shared inline-code styling and the audio
picker's bullet separator. The description beneath Languages is removed.

`spellcheckUseSystemLanguages` distinguishes automatic mode from an empty manual
selection. Older configurations retain their original inferred mode. Removing the
last manual language persists checking as disabled, clears Electron's active
language list and leaves manual selection available; selecting a language enables
checking again. Empty selections survive tab/window reloads instead of silently
reverting to system dictionaries.

Evidence and a runnable build are under `/mnt/storage/dpe-language-rows-20261101/`.
Lint, 505 Python tests, 215 JavaScript tests and 33 native settings groups passed,
including empty-selection persistence, re-enabling, heading spacing and metadata.

## Dictionary caching and tray row feedback

Language labels now use `Afrikaans (af)`, with the code sharing the muted styling
of mod version labels. The second line only contains dictionary status; the
inline-code styling and separator have been removed.

Chromium retains ownership of versioned dictionary downloads, validation and
persistent files. DPE's status metadata uses an asynchronous, single-flight
filesystem scan with a 30-second freshness window. Dictionary events invalidate
that window, explicit refresh bypasses it, and temporary scan failures retain
the last good results with a five-second retry interval. A metadata error does
not fail Discord's spelling IPC. Unchanged language selections no longer
reinitialize dictionaries on every Discord locale notification.

The Spelling tab keeps a shared snapshot across tab changes and revalidates on
mount, focus, visibility and while visible. Reads cannot overwrite newer saves;
subscriptions attach before paint so cached controls are immediately usable.
The cache is scoped to the settings renderer and Electron session, while the
dictionary files remain in Chromium's profile cache across process restarts.

The existing tray registration hook now also passes Discord's six original
images. Previews use those paths/native images directly and return after Reset.
Before Discord starts, the launcher supplies the installed module's original
asset directory so onboarding Settings also shows all six default icons.
The introductory PNG paragraph and detached success message are removed. Each
row temporarily displays progress, success or failure below its name, returning
to Custom image or Discord default after three seconds (eight for errors).
Canceling the picker leaves the row unchanged. Timers are cleared on unmount.

Evidence and the runnable build are under
`/mnt/storage/dpe-dictionaries-tray-20261101/`. The native spelling probe restarts
with HTTP(S) requests blocked and checks bilingual suggestions, replacement,
personal-word persistence and zero dictionary download attempts. The settings
harness checks all six default previews, canceled selection, import, timed row
feedback, error recovery, reset and immediate edits after tab changes.

### better-ui follow-up

| Severity | Location                             | Before                                            | After                                                            | Why                                                        |
| -------- | ------------------------------------ | ------------------------------------------------- | ---------------------------------------------------------------- | ---------------------------------------------------------- |
| MEDIUM   | `integration/updater/ui/desktop.jsx` | Boxed language codes on the metadata line         | Muted codes beside the name, matching mod rows                   | Keep the established label treatment                       |
| MEDIUM   | `integration/updater/ui/desktop.jsx` | Detached tray messages and missing stock previews | Feedback in the affected row and actual Discord default previews | Make action results and current selection visible together |

No actionable UI-polish findings remain in the inspected states. Native dark
settings screenshots cover language labels, tray defaults and errors; existing
settings checks cover keyboard use, zoom and theme tokens. Screen-reader output
and signed-in voice tray transitions are not verified. Approve for that coverage.

Final validation passed lint, 505 Python tests, 223 JavaScript tests and 34 real
Electron settings groups, including default previews before tray registration.
The offline spelling restart passed with zero downloads. Run
`/mnt/storage/dpe-dictionaries-tray-20261101/test.sh` for the rebuilt app using the
previous isolated manual profile. The staged sources and UI bundle match the
verified working tree; no hibernation is scheduled.
