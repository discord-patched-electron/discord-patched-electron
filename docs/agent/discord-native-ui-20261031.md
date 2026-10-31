# Discord-native settings and audio UI qualification

The 31 October follow-up replaces the earlier approximate layout with measurements
from the user's already signed-in Canary test client. Native Voice & Video,
Developer settings, dropdowns, sidebar and window controls were inspected directly.
Only application windows were captured. No login, messages, calls or physical
camera capture were performed.

## Design evidence and implementation

Anonymized reference screenshots, computed styles and loaded presentation exports
are in `out/discord-exact-reference/`. Current Discord product context also includes
its [desktop refresh](https://discord.com/blog/player-release-q12025) and
[display settings guidance](https://discord.com/blog/making-discord-on-desktop-look-just-right-display-settings-to-ease-the-eyes).
The live client is the measurement source; these articles are not a pixel specification.

| Element         | Measured implementation                                              |
| --------------- | -------------------------------------------------------------------- |
| Window titlebar | 32px, right-aligned minimise/maximise/close controls                 |
| Sidebar         | 253px before user zoom, 40px navigation rows, 20px icons             |
| Content header  | 49px, 16px/20px medium text                                          |
| Controls        | 40px combobox/button, 8px corners, 48 × 24px switch                  |
| Typography      | Live gg sans faces, 16px labels, 14px descriptions                   |
| Switch motion   | Current Discord's 300ms continuous thumb/pressed-state interpolation |
| Audio modal     | Current ModalRoot's 200ms ease-out quadratic fade and scale 0.7–1    |

The running client supplies semantic colors, theme classes, fonts, reduced-motion
preference and zoom. The signed-in comparison used Onyx and 110% zoom. Font assets
come only from allowlisted Discord asset URLs without account credentials. Theme
changes suppress transitions and are refreshed while settings is open. Arbitrary
third-party CSS selectors are not copied into the standalone window.

One shared registry reads Discord's loaded pure SVG exports and rendered controls.
Transparent secondary layers are preserved. Missing drawings use pinned Bootstrap
Icons 1.13.1 under MIT, with the complete license shipped alongside the registry.
No proprietary SVG snapshots are bundled. Availability of official icons depends
on which presentation modules the running client has loaded.

DPE Settings has flat rows, five text-only tab labels, a profile-style project menu,
versions, native window actions and contained scrolling. Flags uses the shared
comboboxes and switches. Experimental/crash indicators use icons with accessible
names. The recovery command includes its copy button and feedback. Troubleshooting
has one diagnostic list with unobtrusive chrome:// addresses.

Mods offers Vencord, Equicord or no mod on first launch and in Settings. Installation
has pending/error/restart states; existing mod settings and package-update survival
are preserved. The pinned upstream OpenAsar files are unchanged.

The custom audio modal retains its DOM during refresh. Search, selection, focus
and scrolling survive new source lists; cancel and stale responses are guarded.
System Audio omits the automatic-detection link. Opening, interrupted closing and
backdrop motion use the measured native behavior. A bounded preference lookup
prevents a first-frame scale for reduced-motion users. Unknown/late preference
falls back to a cross-fade. Blank application metadata gets readable fallback text.

## UI polish and component stress report

`better-ui` was applied, with the user's measured Discord conventions taking
precedence over generic button or entrance-animation recipes. Hover is gated by
pointer support; transitions name their properties; motion can retarget; overlays
contain scrolling; reduced motion retains opacity feedback.

The current `break` report covers 20 component families: switches, comboboxes,
mod rows, the Advanced disclosure, flags and CSS inputs, recovery/copy, project
menu, navigation, window controls, diagnostic actions, flags actions, mod actions,
version/status/error outputs, audio cards, source grids, search, tabs and audio
actions. It imports production
factories, markup, CSS and fonts. Fixtures use no accounts, devices or installers.

Variable text includes long and unbroken strings, emoji, RTL, mixed direction and
tall scripts. Relevant quantity tests use 0/1/3/30 items. Widths include 320px,
constraints from siblings and wide containers. Fixed authored labels omit arbitrary
text variation. An empty recovery command is excluded because the backend always
supplies a valid command. Interactive fixtures remain available in the self-contained
`out/dpe-break-report.html` and `out/dpe-break-components/`.

| Scenario             | Observed                                               | Owner and resolution                                                                                  |
| -------------------- | ------------------------------------------------------ | ----------------------------------------------------------------------------------------------------- |
| Empty audio metadata | Blank source card label and description                | `better-writing`: render Application / Application audio, verified in the production Electron fixture |
| Long mod version     | Accepted unbroken version could overflow a narrow row  | Wrap the version within its text column; rerun the narrow component scenario                          |
| Clear search button  | Inherited text-button minimum consumed the input width | Remove that minimum for the icon control; verify usable input width with nonempty search              |

No other clipping or overlap was observed in the reviewed scenarios. Dynamic
keyboard, clipboard, loading, errors, animation and cancellation are covered by
the separate regression fixtures; the static stress skim does not establish them.
Harness-only template escaping and ID/style mismatches were corrected before
recording component findings. Browser preview height limits required separate
component pages and a continuation for the CSS textarea cases.

## Qualification

`out/validation-storage/discord-native-final/report.json` records the successful
full working-tree matrix. This path points to the storage drive because the root
filesystem was nearly full. It reused the approved retained Electron runtime.

- Lint, 414 Python tests and 145 JavaScript tests passed.
- All 21 settings groups passed, including keyboard comboboxes, switch reversal,
  copy feedback, setup, persistence, diagnostics, flags conflicts, RTL and zoom.
- Audio checks passed selection, refresh without remount/animation restart,
  cancellation, delayed assets, reduced motion, backdrop and keyboard containment.
- Six viewport layouts and nine zoom/font combinations passed; the online fresh
  profile passed Discord's four base themes.
- Portable build, all four package formats, first launch and relaunch passed.
- No-bundled-Electron startup passed with both cold downloads and cached runtimes.
- Signed application update and both official mod installers/self-updaters passed,
  including preservation, relaunch and uninstall in fresh profiles.

Final indicator placement, the rendered-chevron selector and blank-metadata fallback
were subsequently checked with lint/unit tests and affected settings/audio probes.
The final manual build is `out/validation-storage/discord-native-reviewed-final/`.
These last changes do not alter installation, update or packaging behavior.
Final focused evidence is in `out/dpe-audio-reviewed/result.json` and
`out/validation-storage/discord-native-reviewed-settings/settings.json`.
A separate default-display run exposed a centering assertion that ignored scrollbar
width at 640 × 480. The fixture now waits for entrance completion and compares
children with the available content area; the qualification uses its explicit
1440 × 1000 virtual display.

### Follow-up to the 18:38 reference screenshots

The later pass replaces screenshot-derived constants wherever Discord exposes
native values. Controls consume its `--space-*`, `--size-*`, `--radius-*`,
`--control-input-height-*`, `--control-item-height-*` and font-weight tokens.
Settings inherits the actual root font size as well as Electron zoom; these are
separate values and are not inferred from screenshot pixels. Typography and
settings geometry come from visible native controls. The native XL modal width
and scrollbar width come from currently loaded stylesheet declarations, using
semantic class prefixes rather than a pinned build hash. Missing, conflicting,
invalid or inaccessible values keep bounded fallbacks or the last valid value.
Source-card dimensions and the source-picker height still have responsive
fallbacks where that lazy native component is unavailable. They are not claimed
as public Discord design tokens.

The primary audio button now sizes intrinsically from its native font and padding.
The modal uses the native surface token, control heights and spacing, with a
borderless secondary action. Escape, backdrop dismissal and Continue without audio
all stop the helper and dispatch Discord's loaded stream-settings action with
`soundshareEnabled: false`. The action's partial-update behavior was inspected in
the live client; no quality settings or capture-start actions are changed. Unit
fixtures cover stale replies, re-enabling audio and a missing/rejected action.

The settings version footer identifies the active Discord host/channel, Linux
distribution/kernel and actual installed mod revision. Mods uses original project
logos and Install/Installed buttons; Vencord's official dark logo variant follows
the inherited theme. The Advanced disclosure uses the native show/hide pattern,
experimental indicators precede labels, and Custom CSS fills the available pane.
The external-link, app, screen, refresh, chevron and experimental glyphs resolve
to loaded Discord SVG exports, with the existing licensed fallbacks retained.

The first combined follow-up run caught an obsolete onboarding selector in the
test helper. It expected the old radio group after the real UI had moved to a
Continue without a mod button. The helper now drives that button directly.

`out/validation-storage/discord-token-final/report.json` passed the full matrix:
lint, unit tests, portable/package builds, setup/relaunch, audio, four live themes,
cold and cached startup without bundled Electron, signed application update and
both official mod installers/self-updaters. Final verification passed 417 Python
and 153 JavaScript tests, plus all 23 settings groups. Small subsequent corrections
to content-box geometry, long-version wrapping and the search icon minimum were
rechecked with lint, the complete unit suites and affected Electron fixtures:

- `out/validation-storage/discord-corrections-reviewed-settings/settings.json`
- `out/validation-storage/discord-corrections-reviewed-audio/results/result.json`

The final manual build is `out/validation-storage/discord-corrections-reviewed/`.
The source-hashed component report is `out/dpe-break-report.html`; its provenance
distinguishes current observations from superseded source revisions. Native audio
comparison at the user's 110% zoom is in `out/dpe-audio-native-tokens-110/`.
The live metadata check correctly showed the manually updated test client's
Canary 1.0.2391, while fresh pinned profiles used Canary 1.0.2357.

Not verified: live-call quality, every third-party theme, a screen-reader listening
session, physical touch or 10%-speed DevTools playback. The reference screenshots
and computed styles establish the inspected current client, not all Discord rollouts.

## Component and onboarding follow-up

The later implementation replaces the settings document/controller with locally
bundled Preact components. General, Appearance, Mods, Flags, diagnostics, navigation,
Welcome and shared controls have named modules under `integration/updater/ui/`.
The audio search is a Preact component that retains its input and focus while
filtering or refreshing. Its clear action uses the same native-icon registry and
licensed fallback as the other controls. Packaging includes the runtime and MIT
notice; installed clients never fetch executable UI code from a CDN.

Welcome now appears before Discord's bootstrap, with no settings sidebar. It has
mod selection followed by Configure more or Done. The pending marker survives a
closed wizard, and Done restarts through the normal launcher so changed mods and
flags apply. A visible Welcome acknowledges application health instead of causing
an update timeout while the user chooses. The fixed portrait window waits for its
first rendered controls, logos and fonts; icon slots do not change geometry later.

Mods has inline muted versions and per-row Install/Uninstall actions. A reserved
progress area reports real download byte totals where available and named stages
otherwise. Failed switches preserve the installed mod. Backend-owned setup markers
cannot be overwritten by a stale General or Appearance snapshot. Regular labels
are not selectable; editors and recovery commands remain selectable. The project
menu closes on its second trigger click. The version footer copies all metadata,
and Discord's own settings regain a separately clickable DPE version line.

Explicitly re-enabling stream audio now bypasses automatic PID selection for that
capture generation. Initial capture still supports automatic detection. Tests cover
both source/transport call orders, repeated hints, cancellation, retries, replacement
sources and stale helper replies. No live-call claim is made by these fixtures.

Fresh signed-out research loaded Discord's settings presentation chunks to inspect
its actual panel rule, without account actions. The panel is centered at a maximum
696px content width, with `--space-16` inline padding and native scrollbar gutters;
vertical space uses `--space-64` or the compact `--space-80` rule. Appearance uses
the same page insets. Text links consume `--text-link` and underline on hover.
Evidence and native asset URLs are in
`out/validation-storage/settings-spacing-research/rules.json`.

Earlier initial-map niri checks confirmed the fixed Welcome floats immediately. Resizable
Settings remained tiled despite Electron reporting a real parent, including a
separate modal-parent probe. No post-map float commands or compositor configuration
changes were made. Settings retains legitimate parent/dialog hints; affected
Wayland installations then needed an initial title-based compositor rule. The
first-focus fix below supersedes that limitation on niri. Hyprland was
not installed here and was not tested. niri's documented criteria are in its
[floating-window guide](https://niri-wm.github.io/niri/Floating-Windows.html).

The rebuilt stress report contains 22 separate component families using actual JSX
and picker factories, including progress and the two-step Welcome. No additional
clipping or overlapping content was observed. The first fixture used unitless
numeric widths under Preact 11; those harness dimensions were corrected before
recording observations. The report uses the production textarea fallback, while
Monaco and full window behavior are checked in Electron. Continuation views cover
cases beyond the preview height limit.

The final full matrix passed all 16 stages: lint, 420 Python tests, 171 Node
tests, the portable build, 24 settings check groups, first launch and relaunch,
audio fixtures and live Discord theme checks, four package formats, cold and
cached no-Electron startup, signed application updates, and both official mod
installers with self-update, package-update and uninstall preservation. The
matrix and manual app are under
`out/validation-storage/discord-component-final/`. Footer-specific lifecycle,
keyboard and native-theme checks are in `out/dpe-settings-footer-final/result.json`.
The floating limitation is recorded in
`out/validation-storage/floating-first-map/niri.json` and the modal follow-up.

## Automatic restart after a Welcome install

A successful Vencord or Equicord install from Welcome now saves the completion
marker and immediately relaunches through the normal launcher. Progress reports
finishing setup and restarting Discord. Failed installs stay retryable; if saving
completion fails, the installed mod remains visible and Done can retry. Installing
from the ordinary Settings window keeps its existing manual-restart behavior.

The first Electron probe caught the shared Flags handler opening an unsaved-flags
dialog during Welcome's automatic close. Welcome no longer attaches that editor
or its close prompt. The installation close guard yields only to the authorized
completion path. Both mod choices, failure/retry, busy protection and persisted
completion have regression coverage.

Follow-up validation passed lint, 420 Python tests, 174 JavaScript tests, all 25
Electron settings check groups, a fresh portable build, and first launch/relaunch.
Evidence uses `out/validation-storage/onboarding-auto-restart-*-final`; the simple
`out/launch-test.sh` wrapper points to `onboarding-auto-restart-app-final/launch.sh`.
The earlier 16-stage matrix remains evidence for the preceding source revision.

## Welcome font and button follow-up

The welcome copy now names Discord Patched Electron. A fresh signed-out Canary
probe captured native font faces, button rules and theme tokens without account
actions. Evidence is in `out/validation-storage/onboarding-style-research/`; the
native button sheet was
[384209.9b17bb545213239f.css](https://canary.discord.com/assets/384209.9b17bb545213239f.css).
The current gg sans files are pinned by URL, size and SHA-256 in the media manifest,
including real semibold and bold weights. They stay in the user's cache, outside
release packages. Chromium's platform-font inspection confirmed `ggsans-Semibold`
actually renders Welcome, rather than a synthetic bold or system-font substitute.

The shared secondary button now uses its native filled surface and state colors.
Small mod actions use a 32px minimum height, 14px text on an 18px line height and
native spacing tokens. The original generic download fallback uses a 24px grid
and 2px round strokes; a loaded Discord icon takes priority. Welcome's previous
icon lock incorrectly bypassed the entire theme/font capture. It now captures
before first display and freezes only the drawing set afterward.

Real pointer tests also exposed a stale renderer busy guard causing an unrelated
unsaved-flags prompt just after a mod operation. The main-process close guard now
owns installation protection; the redundant renderer beforeunload guard is gone.

UI-polish review against the native control conventions:

| Severity | Location                                  | Before                                                       | After                                               | Why                                              |
| -------- | ----------------------------------------- | ------------------------------------------------------------ | --------------------------------------------------- | ------------------------------------------------ |
| HIGH     | `integration/updater/ui/config-app.jsx`   | A stale busy listener could block closing after installation | Main-process protection owns close behavior         | An interaction must finish cleanly               |
| MEDIUM   | `integration/discord-ui/components.css`   | Transparent secondary buttons and unrelated press scaling    | Native filled states and 50/150ms color transitions | Match the host application's surfaces and motion |
| MEDIUM   | `integration/updater/launch-settings.css` | Small-button padding produced 34px controls                  | Native 32px minimum and spacing tokens              | Keep consistent control density                  |
| MEDIUM   | `integration/discord-ui/icons.cjs`        | Thin 16px download tray                                      | A full-weight 24px arrow/baseline fallback          | Match icon weight to surrounding text            |

Native fallback appearance, real pointer hover/press, fonts, keyboard behavior,
zoom, narrow layouts, install/retry/restart, and the shared audio picker are checked
in Electron. Slow-motion DevTools playback was not verified. Approve for the
inspected controls and states; this is not a claim about every Discord rollout.

Validation passed lint, 420 Python tests, 174 JavaScript tests, 25 settings groups
with verified font bytes and real pointer states, the shared picker regression,
and a portable build with first launch/relaunch. Latest evidence is in
`onboarding-style-settings-reviewed/`, `onboarding-style-audio/result-final/` and
`onboarding-style-launch-reviewed/` under `out/validation-storage/`. The manual
launch wrapper now uses `onboarding-style-app-reviewed/launch.sh`.

## Test and publish commands

From the repository root, rebuild and run the entire fresh-profile matrix:

```sh
uv run python scripts/check_working_tree.py
```

For large outputs, pass `--output` with a fresh directory on the storage drive.
The script prints a fresh-profile manual launch command when it finishes.

The separate publication script is prepared for the maintainer to run:

```sh
uv run python scripts/publish_working_tree.py --plan out/dpe-ui/commit-plan.json
```

It checks the hash-bound plan, lint/tests, creates the reviewed commit groups,
pushes main without force, requests packaging with the approved runtime, waits for
qualification and promotes only the matching source/workflow manifest. It never
promotes a failed workflow. Running it supplies the stable-approval confirmation.
Changing source after review requires regenerating the plan. Publication state
is retained in `out/working-tree-publication.json` for safe workflow-wait retries.
No task changes have been staged, committed, pushed or published.

The final branch inventory is `out/dpe-ui/worktrees-final.json`: all 17 other
worktrees are clean and already ancestors of main. The audio branch is integrated;
origin/ci-state remains the independent discovery-data branch.

Release notes group explicit feat: changes under NEW and separate improvements,
fixes and upstream inputs. Ambiguous historical subjects do not receive NEW tags.

## Explicit onboarding completion and reliable codec setup

The later user request supersedes the earlier automatic restart immediately
after mod installation. Installing Vencord or Equicord now remains on Welcome's
first page until **Next**. The second page offers **Configure more** and
**Restart to finish**. Closing the onboarding-owned Settings child completes
setup and restarts once. Cancelling Welcome does not turn its child closing into
a restart. Duplicate actions are guarded, and failed completion or restart can be
retried. Ordinary Settings windows keep their existing close behavior.

The scoped main-process tests and 26 native Electron UI check groups passed.
The fixture exercises successful and failed installs, explicit Next, child-window
closure, cancellation, repeated actions and completion errors, with the verified
gg sans font cache. See `out/validation-storage/openh264-reliability/settings-final/`.

OpenH264 now has verified acquisition and repair before preload, in its own cache
outside Discord's asset maintenance. See the current
[codec follow-up](software-codecs.md#fresh-profile-acquisition-follow-up-31-october-2026)
for the cause, acquisition policy and sandboxed decoder evidence.

The launchable working-tree build is
`out/validation-storage/openh264-onboarding-reviewed/`; `out/launch-test.sh` points
there. Changes remain uncommitted.

Final validation: 432 Python tests, 177 JavaScript tests, lint, six required
sandboxed software-codec checks, 26 settings/onboarding UI groups, release package
inspection, fresh startup/relaunch, runtime-free startup/relaunch, online
Quickstart codec repair and offline cached startup passed. All other 17
worktrees are clean and their heads are ancestors of main. The reviewed commit
plan was refreshed without staging, committing or publishing.

## Full-window Welcome artwork and pre-startup native presentation

This follow-up replaces the earlier Welcome illustration layout and child-window
handoff. The background is now a 1024 × 1280 Krita canvas: the original square
illustration is unchanged at (0, 176), with a sampled sky continuation above and
grass/path continuation below. The grass strokes use Krita's native Pencil-2
brush. The editable source is [welcome-background.kra](../../artwork/welcome-background.kra);
the [artwork guide](../../artwork/README.md) records the layers and export target.
No generated image is included. The raw original-pixel comparison passed during
the Krita export. Evidence is `onboarding-artwork/krita-export.json` under
`out/validation-storage/`.

The illustration covers the whole Welcome window, including the titlebar and
footer. Three masked backdrop-blur layers and a dark gradient progressively clear
toward the illustration. The footer blurs its backdrop while buttons remain
opaque. Reduced transparency replaces the footer blur with a solid surface.
Next now tracks a metadata operation separately from installation progress, so
it cannot briefly reveal the install progress bar.

Configure more creates Settings independently, waits for it to be ready and closes
Welcome. The packaged X11 check exposed Electron's renderer `window.close()`
bypassing the interceptable BrowserWindow close event. The titlebar and Escape
now use the existing restricted native close action, preserving completion and
unsaved-edit handling. Only the onboarding Settings window shows
“Closing settings will launch Discord.”
above Restart Discord. Closing it completes setup and restarts once; failed
completion leaves it open to retry. Unsaved flag edits still receive the ordinary
discard confirmation. Second-instance activation focuses the surviving owner.

Before any desktop Discord bootstrap, a sandboxed, nonpersistent public-shell
helper captures native presentation. The measured fresh acquisition took 6.36
seconds and returned 2,124 tokens, 20 font faces and 18 icons. Its API and auth
socket requests were blocked, it had no native bridge, and it was destroyed
before showing Welcome. Offline replay returned the identical snapshot and all
20 cached font files without another helper or network access. Cached user theme
and zoom take precedence over public defaults. Desktop window glyphs and icons
absent from the public shell retain explicit fallbacks. See
`native-presentation-probe/native-helper-evidence.json`.

The `better-ui` pass follows Discord's existing controls and motion conventions:

| Severity | Location                                      | Before                                               | After                                                                    | Why                                                   |
| -------- | --------------------------------------------- | ---------------------------------------------------- | ------------------------------------------------------------------------ | ----------------------------------------------------- |
| MEDIUM   | `ui/mods.jsx`, `ui/onboarding.jsx`            | Next briefly showed installation progress            | Metadata and install operations have separate states                     | Feedback describes the actual operation               |
| MEDIUM   | `launch-settings.css`                         | Small illustration interrupted the page surface      | Full-window artwork with progressive shading and an opaque-button footer | Consistent surfaces and readable text                 |
| MEDIUM   | `presentation.cjs`, `native-presentation.cjs` | First-run controls could use generic icons and fonts | Native acquisition, cached replay and readiness gate before show         | Consistent icons and typography without layout shifts |
| LOW      | `launch-settings.css`                         | Hidden labels caused one-pixel RTL overflow          | Logical placement without negative margins                               | Hidden accessibility content must not alter layout    |

The regular settings fixture passes 27 check groups, including a mutation
observer checking that Next never reveals progress, artwork bounds and blur,
Welcome destruction/Settings ownership, the conditional closing hint, failed
installs, dirty flags, keyboard navigation, reduced motion, narrow layouts and
RTL. Its account-free screenshots and report are in
`onboarding-artwork/settings-delivery/`. The native-asset fixture additionally
passes 40 assertions and captures 18 views across both Welcome pages and Settings
at default size, 320px, 200% zoom, reduced motion/transparency, RTL and RTL at 320px.
The compact closing hint gets its own row; the top gradient follows the copy's
actual bounds so zoomed text retains its dark backdrop. Evidence is in
`onboarding-native-delivery/`. Slow-motion replay of every inherited
Discord animation was not performed in this follow-up; no new animated blur or
entrance sequence was introduced. Approve the inspected surfaces and states.

The launchable build is `out/validation-storage/onboarding-krita-ready/`, selected
by `out/launch-test.sh`. Packaged Configure more/Settings-close and direct Restart
to finish both reach the native Discord login page, with setup complete and no
desktop client loaded before finishing. Relaunch skips Welcome. The fresh-profile
reports are `onboarding-artwork/launch-delivery/` and `launch-ready/`.

The full suite also exposed a test-observer race in process cleanup: procfs can
return ESRCH when a process exits between opening and reading its stat file. The
test now recognizes that alongside ENOENT as an exited process; production
cleanup behavior is unchanged.

Final validation passed: lint, 432 Python tests, 189 JavaScript tests, 27 settings
check groups, 40 native presentation/UI assertions, package inspection, both
onboarding completion paths and startup/relaunch without a bundled runtime. The
runtime-free evidence is `onboarding-artwork/no-electron-ready/`; it downloaded
the verified runtime rather than seeding a local archive. No account login,
message, call or physical camera test was performed. All 17 other worktrees
remain clean and included in main. The reviewed publication plan is refreshed;
no changes were staged, committed or published.

## Version footer and initial Wayland floating

The native footer tooltip uses its hidden marker's preceding element as its
anchor. Inserting the DPE button directly after Discord's version target displaced
that anchor, so the Copied tooltip aligned with the DPE line. The injection now
keeps the target, marker and accessible description together. The independent
DPE action follows those nodes, uses `--space-4` for the native compact-row gap,
and has a regular cursor while preserving Enter, Space and visible keyboard focus.

A fresh signed-out Canary shell rendered Discord's actual private footer using
its own React components and tooltip layer. This did not open account settings
or sign in. The tooltip's horizontal offset from its native anchor changed from
30.34px to 0.34px; the line gap changed from 0 to the theme's 4px. Source, CSS,
before/after geometry and app-only captures are retained in
`out/validation-storage/settings-footer-floating/research/`. The offline footer
fixture now runs as part of `scripts/check_launch_settings.py`, covering late
mounts, native text styling, marker adjacency, token changes, repeated injection,
keyboard activation, mod duplicates, remounts and missing optional tooltip nodes.

Electron's parent hint did not establish a Wayland transient parent in the
retained runtime. Settings now starts with fixed sizing on native Wayland, then
restores resizing on its first native focus. Restoring during `show` or the next
main-process turn proved too early: niri still tiled those probes. First focus
occurs after the initial compositor mapping, so the final approach needs neither
a post-map floating action nor a user window rule. An unfocused new Settings
window retains fixed sizing until focused; early close removes the focus listener.
Welcome remains fixed. Explicit X11 launches retain the prior resizable behavior.

Production-window probes on niri 26.04 (v26.04-233-g3af9296f), Electron
46.0.0-alpha.1 confirmed normal Settings, Welcome and the independent
Configure-more replacement all report `is_floating: true` on their **first**
`WindowOpened` event. Both Settings windows then resize, maximize and restore.
Closing the replacement completes onboarding exactly once. Evidence, source
hashes, filtered compositor events and protocol trace are in
`out/validation-storage/settings-footer-floating/current-final/`. No compositor
configuration was edited. Hyprland is not installed and remains unverified.

| Severity | Location                              | Before                                                        | After                                                                 | Why                                                         |
| -------- | ------------------------------------- | ------------------------------------------------------------- | --------------------------------------------------------------------- | ----------------------------------------------------------- |
| MEDIUM   | `integration/discord-ui/settings.cjs` | DPE line had no native gap and intercepted the tooltip anchor | Native spacing token, default cursor and preserved tooltip-node order | Predictable spacing and copy feedback at the correct target |
| MEDIUM   | `integration/updater/config.cjs`      | Resizable Settings tiled on native Wayland                    | Fixed initial constraints released on first focus                     | Requested initial floating layout with subsequent resizing  |

Approve for the inspected footer and niri window behavior. Settings integration
passed all 28 groups, including the new footer regressions. Required full-suite,
lint and rebuilt-app evidence is in `out/validation-storage/settings-footer-floating/`.

## Final upgrade and release review

Settings now defaults to 1100 × 800 within the available work area. Window
controls are local SVG geometry; presentation capture no longer collects those
icons. Closing Welcome uses the same save-and-launch path as finishing setup.
The actual upgrade fixture exposed Electron quitting when the last window was
closed before the completion worker finished. The close handler now defers
native destruction until completion, preserves retryable errors and cancels a
pending Configure-more handoff before proceeding.

Upgrades with no completed setup marker and no currently installed mod now see
Welcome. The bounded, read-only active-loader detector distinguishes installed
Vencord/Equicord from old backups; probe failures skip this optional prompt.
A fresh profile seeded with legacy settings verified upgrade Welcome, clicking X,
launching Discord and skipping Welcome on the next launch. Evidence:
`out/validation-storage/release-upgrade-welcome-fixed/`.

Independent final reviews covered UI/audio injection and lifecycle, installer
transactionality, download retries, signed update discovery and release staging.
No remaining blockers were reported. All 17 other worktrees were clean and
already included in main. Aggregate test and packaging evidence is under
`out/validation-storage/release-final-20261031/`; the final close-path unit suite
and lint are `out/validation-storage/release-tests-final.log` and
`out/validation-storage/release-lint-final.log`. Publication is a test candidate
using the approved runtime, with stable approval left for later manual testing.
