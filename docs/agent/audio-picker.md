# In-window audio picker

For current cross-desktop automatic detection and its off switch, see
[audio auto-detection](audio-auto-detection.md). Earlier detection notes below are historical.

## Current custom picker (31 October 2026)

The production selector now uses `audio-picker.cjs` directly. Its closed shadow
root contains a browser modal, native HTML controls and the reusable Discord-style
primitives in `integration/discord-ui/components.css`. It inherits Discord's live
semantic theme tokens, gg sans font, root text size and Electron zoom. It no longer
looks up, initializes or waits for Discord's private React components.

The existing source ranking, application preselection, search, stable footer,
refresh, keyboard containment and scope descriptions remain. Closing the host
cancels the request, and one unreadable application icon cannot stop later icons.
Zenity remains the fallback if the in-window dialog cannot open. The legacy native
adapter and its contract tests are retained as historical probes, outside the
production opening path.

`tests/audio/picker-smoke.cjs` checks the production controls with isolated
fixtures. `tests/audio/discord-picker-smoke.cjs` checks them in a fresh signed-out
Canary window, using its actual Ash, Dark, Onyx and Light styles without invoking
capture or screenshare controllers. See the [UI validation and polish
review](discord-native-ui-20261031.md#audio-picker-and-shared-primitives).

## Historical native-component implementation

The Wayland voice wrapper displays its manual audio selector in Discord's main
renderer. The native path preserves the approved wide screenshare layout using
Discord's bare ModalRoot (size xl), pill SegmentedControl, SearchBar, Clickable
source cards, scroller, icons and buttons. It does not use the compact form Modal
or underline TabBar. Our scoped layout CSS uses a 50 × 35 rem panel, 12.5 × 10 rem
cards, 15 × 2.5 rem search field and corner footer (800 × 560, 200 × 160 and
240 × 40 CSS pixels at Discord's default font size). Native components supply their
interaction behavior; the dialog follows Discord's current theme.

### Screenshare reference correction (2026-10-31)

Discord's own screen/window source tiles use `Clickable`, not a separate exported
card component. Inspection of the current screenshare presentation CSS confirmed
`--background-base-lower`, `--interactive-text-default`,
`--interactive-background-active`, `--interactive-text-active`, the corresponding
hover tokens, a subtle border and a brand-colored selected border. Both audio
renderers now use those tokens, a 0.25 rem radius and no selected checkmark.
No hashed class names or module IDs from that inspection enter production code.

The first offered application is selected. The helper ranks a matching native
capture hint first, then running audio, media roles and applications with windows;
ties use the label and PID. Ranking only suggests a source: confirmation still
requires Share. Search selects the first visible match if the previous selection
is filtered out; no matches disable Share. Switching tabs preserves the application
selection. System audio stays selected and cannot be toggled off.

Refresh remains visible and disabled on System Audio. Share keeps its label and
width in both tabs. The hidden search slot and overlapping footer descriptions
reserve the same space, including when the descriptions wrap. Only the active
description and documentation link are visible and focusable. Symmetric scrollbar
gutters center the heading, search and cards; the dialog is centered in the viewport.
The first card row retains its position when search finds nothing. Longer lists
scroll from that same anchor.

Reference comparison (screenshot proportions estimated; compact rem dimensions
retained to respect the earlier scaling correction):

| Detail           | Discord reference                        | Built picker                                               |
| ---------------- | ---------------------------------------- | ---------------------------------------------------------- |
| Source selection | Filled tile, thin brand border, no tick  | Same native theme tokens and selection treatment           |
| Centering        | Heading and cards centered in the dialog | Heading, search and card row centered within 1 CSS pixel   |
| Footer           | Corner summary and stable action group   | Choose audio source, Share and fixed Refresh slot          |
| Scale            | Follows Discord UI settings              | 50 × 35 rem panel with viewport limits and wrapping footer |

UI polish findings:

| Severity | Location                                                                             | Before                                                         | After                                                        | Why                                                           |
| -------- | ------------------------------------------------------------------------------------ | -------------------------------------------------------------- | ------------------------------------------------------------ | ------------------------------------------------------------- |
| MEDIUM   | `integration/audio/native-picker.css`, `integration/audio/audio-picker.css`          | Tick overlay, incorrect selected fill and off-center content   | Discord source-tile tokens, no overlay and symmetric gutters | Match the supplied screenshare reference.                     |
| MEDIUM   | `integration/audio/native-picker-renderer.cjs`, `integration/audio/audio-picker.cjs` | Changing action labels and disappearing Refresh moved controls | Share and Refresh retain their slots across tabs             | Keep pointer and keyboard targets stable.                     |
| MEDIUM   | `integration/audio/pipewire_audio.py`                                                | Picker opened with no suggested application                    | Rank offered sources and preselect the first app             | Reduce manual selection without starting audio automatically. |

Validation: 396 Python tests, 134 JavaScript tests and repository lint pass.
Actual native and fallback Electron smoke checks cover source selection, empty
search, exact footer-action coordinates across tabs, native contract failures,
keyboard navigation, 80 long labels, six viewport layouts, font size and zoom.
Evidence: `out/picker-reference-fix-native-final/`,
`out/picker-reference-fix-fallback-final/` and `out/picker-reference-css/rules.json`.
No account, live call or physical camera was used. Custom themes and assistive
technology remain unverified. Approve — inspected states only.

### Discord UI scaling

Discord's zoom setting calls Electron's zoom API; its font-size setting changes
the root element's font size. Both picker renderers use `rem` dimensions, spacing,
text and artwork so they follow that root size as well as the host's zoom. There
is no separate transform, cached scale or extra multiplication of Discord's zoom.
Viewport limits and a wrapping footer keep actions reachable when enlarged.
Thin borders and focus outlines retain their normal CSS-pixel thickness.

The native and fallback smoke suites change these settings while the picker stays
open: 80%, 125%, 150% and 200% zoom; 75%, 125%, 150% and 200% root font size; and
125% zoom combined with 150% font size. They assert card, text, icon, search and
button measurements, horizontal containment and footer reachability. Evidence:
`out/picker-ui-scale-native-verified/scaling.json` and
`out/picker-ui-scale-fallback-verified-2/scaling.json`. The tests exercise the same
rendering mechanisms without changing an account's saved accessibility settings.

Historical validation for the earlier scaling change: both scaling suites and
all 59 audio unit tests passed. That full test run passed
387 Python tests and 118 of 121 JavaScript tests; three OpenAsar settings tests
fail in concurrent updater changes. Repository lint reports formatting errors in
seven updater/settings files outside this audio change; scoped audio formatting
and `git diff --check` pass. The earlier all-green run below predates those edits.

### Earlier compact layout and Zen artwork correction

The screenshot's physical dimensions had been applied directly as CSS pixels,
making the picker too large. Both renderers now use the compact dimensions above.
The heading and search use fixed spacing, and the result region reserves one card
row even when empty. System audio retains its automatically selected, non-toggleable
behavior, with the same filled card and brand border as a selected app. The
checkmark used in this earlier revision was removed by the reference correction above.

The footer reads “Includes windows from the same app · Automatic detection”; its
link targets the user guide's new automatic-detection section. System audio keeps
its own scope text. The separator wraps with the link on smaller viewports.

Zen's installed `zen.desktop` points at `zen-browser`, whose hicolor icon is a
symlink into `/opt/zen-browser-bin`. Icon resolution now permits bounded image
reads from `/opt` and `/usr/lib` package trees, and tries the desktop name without
a binary's `-bin` suffix. Arbitrary paths, remote URLs, non-images, excessive sizes
and symlinks outside allowed roots remain rejected. No desktop command is executed.

UI polish review:

| Severity | Location                                                                         | Before                                                   | After                                               | Why                                                                     |
| -------- | -------------------------------------------------------------------------------- | -------------------------------------------------------- | --------------------------------------------------- | ----------------------------------------------------------------------- |
| MEDIUM   | `integration/audio/native-picker.css:1`, `integration/audio/audio-picker.css:12` | Oversized panel and controls; empty search moved content | Compact dimensions and fixed heading/search spacing | Preserve component density and stable interaction targets.              |
| MEDIUM   | `integration/audio/native-picker.css`, `integration/audio/audio-picker.css`      | System audio lost its card boundary                      | Filled selected card with border, without a toggle  | Borders communicate selection consistently.                             |
| MEDIUM   | `integration/audio/app-icons.cjs`                                                | Zen's installed icon fell back to a generic glyph        | Resolve package symlinks and binary-name variants   | Use recognizable application artwork without changing capture identity. |

Validation covers real native and fallback rendering, empty-search coordinates,
selected system cards, docs-link dispatch to the host window-open handler, Zen's
installed artwork, keyboard navigation, six viewport/zoom cases and 80 long labels.
Evidence: `out/picker-compact-native-verified/` and
`out/picker-compact-fallback-verified/`. All 387 Python tests, 121 JavaScript tests
and lint pass. Earlier dimensions and counts below describe historical runs.
Not verified: live calls, assistive technology, custom user themes, external browser
launch for this link, and slowed animation playback. Approve — inspected states only.

`native-picker-discovery.cjs` supplies audio component contracts to the
[shared Discord UI runtime](discord-ui.md), which also hosts the OpenAsar settings
entry. Both use signature-based discovery without fixed module IDs. The pill control is part of the lazy screenshare chunks. Only
its presentation module is initialized if registered; the screenshare controller,
source enumeration and capture hooks are never invoked. If those chunks or
components are unavailable or rendering fails, `audio-picker.cjs` uses the
original closed-shadow-root DOM picker with the same design. Zenity remains the
last fallback. No page-accessible Node API or IPC handler is exposed.

The helper sends bounded, display-only application/media labels, a generation,
and a request number. Replies select only an offered process whose identity is
still live, or explicitly select system audio. Refresh creates a new request;
old replies cannot select a source. Cancellation, source disappearance, helper
exit and connection destruction remove the modal. Escape continues without
audio. Refresh discovers streams that appeared after the dialog opened.

### Resilience to Discord updates

Discovery, rendering and lifecycle management are separate adapters:

- The shared `integration/discord-ui/runtime.cjs` inspects webpack exports, while
  `native-picker-discovery.cjs` selects React and modal APIs
  instead of picking the largest cache. It also recognizes renamed `webpackChunk*`
  globals, nested/default exports, memo and forward-ref wrappers. Components stay
  within one compatible runtime. Throwing getters, cycles and localization proxies
  cannot stop the scan.
- Source signatures use property/literal tokens, tolerating changes in module IDs,
  local variable names, comments, whitespace, quote style and escaped property keys.
  Stable export names can disambiguate structurally compatible candidates. Ambiguous
  matches are rejected instead of choosing the shortest function. Plain and compatible
  imperative scrollers are supported; virtualized lists are excluded. Icons are optional
  and have local SVG fallbacks. A discovery failure is not cached across openings.
- Only a unique, small, already-registered pill presentation module can be initialized.
  Recognized capture/controller calls exclude it. This is a conservative source
  heuristic, not a guarantee about future module side effects. No chunks are fetched by
  production discovery, and discovery never renders candidate components to identify them.
- `native-picker-renderer.cjs` validates the mounted tabs, search value, source choices,
  selection attributes and primary-button disabled state after native child effects run,
  and rechecks after tab/search/selection changes. React errors, missing content and host
  unmounts close the native attempt. Search accepts strings and common input-event shapes;
  tabs accept strings or value objects. Unexpected callback values trigger fallback.
- `native-picker-bridge.cjs` retries missing startup modules at most three times, with
  200ms spacing. Ambiguous or incompatible controls immediately use the original DOM
  picker. A 2.5-second render deadline and independent 4-second bridge deadline bound
  startup promises; both stop after a validated mount, so users can choose at their own
  pace. Cancellation marks the session before a delayed open can run, late replies and
  artwork are ignored, and one failed icon does not interrupt other artwork.

The original DOM picker remains independent of Discord's component discovery and
preserves the approved design. Major changes to the private component APIs, DOM contracts
or theme variables may still require an adapter update. DOM checks detect structural and
state mismatches; they cannot prove arbitrary future event handlers or layout behavior.
Timers cannot recover a completely blocked renderer event loop. No fixed hashed Discord
CSS classes are used, and style rules are scoped to this picker.

### Native component validation

`tests/audio/native-picker-smoke.cjs` uses a fresh signed-out Canary profile and
registers the screenshare UI chunks without invoking its controller or capture.
It verifies the actual native components, original geometry, search/clear,
selection and system audio that cannot be deselected. Keyboard checks include
Tab/Shift+Tab containment, access to the pill tabs, arrow-key source selection
and Escape. Eighty long-named sources remain reachable without horizontal
scrolling at 1269 × 772, 720 × 500, 360 × 640, 320 × 480, 640 × 360 and 200% zoom.
The footer stays fixed when there is room; short windows scroll the full dialog.

Validation: 276 Python tests, 88 JavaScript tests and lint pass. Native UI
evidence is in `out/native-picker-harden-6/`; fallback evidence is in
`out/native-picker-original-fallback/`.

Captures cover Discord's darker and midnight themes. Unit tests cover allowed
replies, fallback, stale results/artwork and teardown. Discovery tests simulate
changed exports, minifiers, runtimes, wrappers, icons and lazy modules; bridge tests
exercise stalled startup, bounded retries, long user decisions and close-before-open
races. The live smoke also injects broken button/scroller implementations and host
unmounts to verify rejection and cleanup. The original DOM smoke
suite continues to exercise the fallback. No live call, assistive technology,
custom user theme or slowed animation playback has been tested.

UI review: the incorrect compact template was replaced with the screenshare
composition. Native modal padding is removed only for this dialog so it cannot
alter the approved insets; native button size and search height retain the
reference measurements. No actionable UI-polish findings remain in the inspected
states. Approve — geometry, native interactions and listed viewport checks only.

Release preparation on current `main` (31 October 2026) preserves its Zenity desktop
identity fix alongside the new inline picker. All 349 Python tests, 105 JavaScript
tests and lint pass on the combined tree. The native smoke passes again in a fresh
signed-out profile; local evidence is `out/prerelease-picker-smoke/` in the original
working checkout. Packaging reuses the latest prerelease's Canary 1.0.2421 client
snapshot and verified Electron 46.0.0-alpha.1 runtime.

## Cross-desktop detection investigation

Reviewed on 31 October 2026:

- The [ScreenCast portal interface](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.ScreenCast.html)
  describes stream IDs/serials, dimensions, source types and mapping IDs. It does
  not standardize the captured window's owning PID. `mapping_id` relates remote
  input regions, not audio ownership. Opening another portal session would ask
  for a second video selection rather than identify Discord's current selection.
- [PipeWire stream properties](https://docs.pipewire.org/page_man_pipewire-props_7.html)
  provide application identity and media labels across desktops. The picker now
  falls back through application name, application ID, binary and node description,
  and shows media names when compositor window titles are absent. Multiple streams
  of one verified PID remain one choice. These labels never authorize capture.
- [MPRIS Identity and DesktopEntry](https://specifications.freedesktop.org/mpris/latest/Media_Player.html)
  and [PlaybackStatus](https://specifications.freedesktop.org/mpris/latest/Player_Interface.html)
  can enrich player discovery, including idle players, but do not map a browser tab
  or portal selection to a playback stream. Not implemented as automatic authority:
  games/non-media apps may not expose MPRIS, and process ancestry would still need
  verification. No media-control commands or track history are collected.
- Existing compositor window inventories remain optional label enrichment.
  niri's exact cast-to-window mapping and native PID hints remain the automatic
  paths; other desktops use explicit selection. Flatpak instance verification
  stays intact. No same-title/app-name merging or focused-window guesses.

The picker lists up to 80 verified audio-owning processes, sorted by application
label. It includes native PipeWire playback and real PulseAudio playback, using
the existing serial-based join for PipeWire-Pulse. Separate processes may appear
as separate choices; this change does not claim universal per-window/tab audio.

## Validation

`bun run test` covers request generations, refresh, cancellation, stale/reused
PIDs, Unicode split across helper messages, fallback and source teardown alongside
the existing routing/isolation cases. `tests/audio/picker-smoke.cjs` exercises the
actual DOM in a disposable Electron profile under Xvfb, with synthetic labels and
no Discord account or host media. Set `DPE_PICKER_OUTPUT` to an output directory;
unset `ELECTRON_RUN_AS_NODE` when launching a retained Electron runtime.

Not verified: live Discord call, real GNOME/KDE compositor, custom user themes,
screen-reader announcements or animated playback at 10% speed. No new Electron
compilation or release packaging is necessary for these source changes.

### Reference-matched layout and UI review

The user's 1269 × 772 screenshot was treated as approximately one image pixel
per CSS pixel. The initial compact modal has been replaced with its wide sharing
layout. Values below are estimates from that image, not Figma measurements.

| Element         | Design                                                        | Built                                                              | Status                                                       |
| --------------- | ------------------------------------------------------------- | ------------------------------------------------------------------ | ------------------------------------------------------------ |
| Outer panel     | About 1200 × 720, 16px corners, near-black                    | 1200 × 720 maximum; Discord theme tokens with near-black fallbacks | Matches reference proportions                                |
| Top navigation  | 50px segmented bar; Applications / Devices                    | 48px bar; Applications / System Audio                              | Audio-specific labels; both tabs work                        |
| Source tiles    | About 254 × 206, 20px gap, muted icons                        | 254 × 206, 20px gap, currentColor application/monitor icons        | Matches estimated dimensions                                 |
| Footer          | Left summary, right primary action and square settings button | Selection summary, Make Selection and square refresh action        | Uses actual audio actions rather than unwired video settings |
| Search and skip | Not shown                                                     | Search above tiles; Continue without audio in footer               | Retained existing picker functionality                       |

Missing design states: selected, hover, empty, search, keyboard focus, light theme
and narrow layouts. These retain existing picker behavior and theme conventions.
Application/media labels in the real picker come from the helper; screenshot
fixtures use Helium and mpv sample data with their installed application icons. No backend features are unwired.

#### Search, labels and artwork refinement

The corner now always says **Choose audio source**, including after selecting a
card. Application scope reads **Includes windows and tabs from the same app.**;
system scope reads **Includes all applications except Discord.** Opening the
System Audio tab selects system audio automatically; its informational illustration
has no radio or toggle. **Share Audio** confirms it. Returning to Applications
requires an app choice, so a hidden system selection cannot carry over. The supplied
search screenshots were interpreted at 1:1: a 274 × 50 field, 10px corners,
magnifying-glass icon, subtle idle border and a 2px brand-colored focused edge.
Search matches application and media labels case-insensitively, trims whitespace,
clears with a named button, resets the results scroll and deselects hidden sources.

Local app icons use audio metadata (`application.icon_name`, application ID,
Flatpak app ID and binary) and desktop-entry `Icon` values. Resolution checks
XDG data locations, hicolor app icons and pixmaps, including Flatpak export roots.
It uses bounded asynchronous file reads, four workers and a per-dialog cache.
PNG/SVG artwork is loaded as an image, not inserted as markup; broken/missing
icons preserve the generic glyph. Closed dialogs ignore late artwork results.
Desktop `Exec` lines are never run; no icon web requests are made.

This is branded application artwork, not live window thumbnails or album artwork.
It avoids another portal request or incidental desktop capture. An application
that supplies neither matching metadata nor an installed icon keeps the generic
fallback. Custom icon-theme inheritance and XPM are not implemented.
See [PulseAudio's application icon property](https://freedesktop.org/software/pulseaudio/doxygen/proplist_8h.html)
and the [freedesktop icon theme specification](https://specifications.freedesktop.org/icon-theme/).

#### UI polish review

| Severity | Location                                                                           | Before                                                                             | After                                                                                  | Why                                                                      |
| -------- | ---------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| MEDIUM   | `integration/audio/audio-picker.css:87`                                            | Plain bright-bordered input                                                        | Reference-sized icon search with separate idle/focused states                          | Match existing surfaces and input state language.                        |
| MEDIUM   | `integration/audio/audio-picker.css:186`, `integration/audio/audio-picker.css:362` | Long labels could enlarge cards; short viewports left too little space for results | Two-line labels, contained result scrolling, whole-dialog scrolling on short viewports | Contain scroll inside overlays; every source and action stays reachable. |
| LOW      | `integration/audio/audio-picker.cjs:195`, `integration/audio/app-icons.cjs:12`     | Every application used the same window glyph                                       | Installed application artwork with a stable-size fallback                              | Recognizable source cards without layout shifts or extra capture.        |

Reviewed initial/disabled, selected, search/no matches, empty applications, hover,
pressed, tabs, icon success/failure, refresh, cancellation and teardown in source.
The tabs retain concentric 16px/12px corners with 4px padding. Actions retain 0.96
press scale over 150ms ease-out under no-preference; theme changes are instant.
Focus remains visible and selection uses a static border/check mark.

Validation: 276 Python tests, 65 JavaScript tests and lint pass. Isolated Electron
checks cover real Tab/Shift+Tab focus containment, real Escape, search/clear,
media search, tab resets, copy, icon decoding/fallback and late-image teardown.
Eighty long-named sources were tested at 1269 × 772, 720 × 500, 360 × 640,
320 × 480, 640 × 360 and 1269 × 772 at 200% zoom. Every size passes horizontal
overflow, last-card reachability, footer reachability, scroll containment and
background-scroll assertions. The footer stays fixed while results scroll at
normal heights; short layouts let the entire dialog scroll to keep actions usable.
Captures and results: `out/audio-picker-search-icons/`, including focused/idle
search details. Visual inspection covered dark, light and narrow layouts.

Not verified: live Discord integration/call, assistive technology, forced-colors
rendering and slowed animation playback. These remain outside visual approval.

Approve — inspected source states and isolated layouts/search/overflow behavior.
