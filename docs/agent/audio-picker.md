# In-window audio picker

The Wayland voice wrapper displays its manual audio selector in Discord's main
renderer. The native path preserves the approved wide screenshare layout using
Discord's bare ModalRoot (size xl), pill SegmentedControl, SearchBar, Clickable
source cards, scroller, icons and buttons. It does not use the compact form Modal
or underline TabBar. Our scoped layout CSS keeps the 1200 × 720 panel, 254 × 206
cards, 274 × 50 search field and corner footer. Native components supply their
interaction behavior; the dialog follows Discord's current theme.

`native-picker-discovery.cjs` discovers loaded components by signatures, without
fixed module IDs. The pill control is part of the lazy screenshare chunks. Only
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

- `native-picker-discovery.cjs` inspects webpack runtimes for React and modal APIs
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
