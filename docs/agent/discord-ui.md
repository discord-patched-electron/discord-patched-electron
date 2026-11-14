# Shared Discord UI integration

`integration/discord-ui/` provides the DPE Settings entry and shared custom UI
primitives. Settings is composed from named Preact components under
`integration/updater/ui/`. The audio search is a Preact component inside stable
picker factories. Neither depends on private Discord component exports to render.
The native audio adapter below is retained
only for historical contract probes.

- `components.css` defines reusable Discord-style buttons, segmented navigation,
  inputs, sidebar rows, switches, notices and semantic color tokens. The settings
  window and isolated audio dialog share it. Discord's live tokens override the
  standalone palette. It is staged beside both consumers and included in the
  application compatibility identity.

Dependent controls belong directly beneath the option that enables/disables them,
using the shared `SettingsDependents` group. Keep their values while disabled.
Select rows include a description when it explains the choice; rows without one
vertically center the title against the control. Keep this behavior in shared
components, including Display flags and PiP settings.

Welcome uses those local components before Discord's desktop renderer starts.
`updater/native-presentation.cjs` obtains current tokens, fonts and icons from a
hidden, sandboxed public Discord shell in a disposable session. Only its login
document and static script, stylesheet and font assets are allowed. API requests,
auth sockets, permissions and navigation are blocked. The helper has no native
bridge or access to the account profile, and closes before Welcome appears.

Sanitized presentation snapshots and verified font bytes are cached per channel.
A previously captured live client theme and zoom take precedence over public
defaults; offline launches reuse the cache. A trusted running Discord window is
preferred when available. Welcome waits for presentation, fonts and local images
before showing, then holds its icons steady while theme changes still apply.
An unavailable or changed upstream shell falls back to the measured local palette,
icons and checksum-pinned gg sans weights 400–700. An offline profile without
cached fonts keeps a system-font fallback. Font files are excluded from packages.
The settings probe accepts `--media-cache` to verify actual gg sans rendering with
a disposable profile instead of silently testing only system-font fallbacks.

The page-world integration remains responsible for the native settings entry:

- `runtime.cjs` owns webpack discovery, component signatures, safe export traversal,
  ambiguity checks and module lifecycle hooks. One runtime instance is shared per
  page through `Symbol.for("dpe.discordUI")`.
- `source.cjs` serializes adapter requests and supplies that shared runtime. It
  exposes no Node bridge or general IPC interface to the page.
- `settings.cjs` extends Discord's user settings layout. It inserts an ordinary
  sidebar action immediately before `logout_sidebar_item`, with Discord's own
  `SettingsIcon`. A separate version-footer adapter restores the clickable DPE
  version beside Discord's copy-build-info control. It reuses only the native
  version span's classes and styles, observes mounts without polling, and adds a
  scoped keyboard focus rule using Discord's focus token.
- `integration/audio/native-picker-discovery.cjs` contains audio-specific component
  contracts. The existing renderer, bridge, cancellation logic and fallback remain
  audio adapters; they use the same runtime and serialization path as settings.

Settings factories are observed after Discord initializes them, before callers
receive their exports. Existing loaded builders are also supported. Future chunk
registration is observed through the original loader, including other mods'
wrappers. The adapter wraps only the user settings root's layout builder, preserves
other sections and constructs a fresh utility list without mutating Discord's
arrays. The native row opens the existing OpenAsar window through its restricted
IPC action. It does not inherit Log Out's destructive variant or callback.

No module IDs are pinned. Discovery never invokes components or initializes
settings controllers. The audio adapter retains its existing exception for one
unambiguous, registered pill-control presentation module. Missing/ambiguous native
settings icons leave the layout untouched; the audio adapter retains its existing
fallback behavior. Upstream changes to the root builder contract may require an
adapter update.

The settings layout approach was checked against
[Vencord's settings integration](https://github.com/Vendicated/Vencord/blob/main/src/plugins/_core/settings.tsx)
and current Canary's native layout builder and sidebar renderer. The implementation
does not require Vencord and does not replace its sections.

## Packaging

`scripts/build_ui.py` bundles the pinned Preact dependency and JSX locally. The
installed application includes `config-app.js`, a self-contained audio runtime,
and the MIT license, without `node_modules` or remote executable dependencies.
Standalone makepkg recipes include these bundles in their checksummed support
archive, so package preparation does not require Bun. Source checkouts build UI
as part of the settings test and packaging commands.

The application stages `integration/discord-ui/`. Voice-module staging copies the
same shared runtime/serializer into its local `discord-ui/` directory, so the
native helper remains self-contained. The bridge resolves that installed copy or
the sibling source directory in a checkout. Both copies initialize the same
page-world singleton. Shared changes enter the application compatibility hash and
select the audio/package CI checks; they do not trigger an Electron rebuild.

## Equicord factory recompilation fix

The first check covered plain Discord but missed Equicord's source-recompiling
factory proxy. Assigning our closure-based wrapper to its module table caused
Equicord to compile the original source and discard our completion hook. The
settings entry consequently never reached the native layout.

The shared runtime now subscribes to `Vencord.Webpack.moduleListeners` when that
API owns the discovered runtime (Equicord exposes the same API). Cached exports
are inspected too. Plain Discord continues to use the standard factory hook.
Source signatures also accept the original source exposed by a callable proxy's
`toString`, so lazy native audio controls remain discoverable with the mod loaded.
Capture-controller exclusions and ambiguity checks still apply.

`tests/discord-ui/equicord-fixture.cjs` loads the actual installed mod bundle with
empty in-memory settings and isolated IPC fixtures. Set `DPE_TEST_EQUICORD_ASAR`
to a disposable archive copy when running the native picker smoke. The user's
mod settings, account data and updater are not used. The settings check recognizes
the patched native layout compiler as well as the unmodified compiler.

## Shared settings behavior

Tray selection uses `trayIconPreset` as its baseline, `trayIconPresets` for
per-state overrides and `trayIcons` for custom PNGs. **Custom** is a derived
selector placeholder, never a saved/selectable preset. Reset removes only the
row's overrides; choosing a new baseline preserves other rows. Asset provenance,
hashes and licenses live in `integration/updater/assets/tray-presets/manifest.json`
and `THIRD_PARTY.md`; image decoding is cached without network requests.

Dictionary removal saves manual selection first, disables aliases sharing the
file and deletes only matching versioned BDic files. Personal words survive.
Download events supply start/success/failure, not byte percentages. Filter options
combine with search and are disabled in system-language mode.

Hidden window controls collapse their slots. With all controls hidden, restore
the native top bar's token-based trailing inset; preserve RTL and live token
changes. Preserve [native window-control behavior](desktop.md#desktop-preferences).

The main renderer populates the shared presentation cache without opening Settings.
Retries for late native assets are bounded to thirty seconds; reinjection and
navigation must not leave duplicate timers.

Troubleshooting opens allowlisted, sandboxed diagnostic windows in the running
Electron session. Repeated requests reuse the window; local-main-frame IPC
rejects unknown pages. GPU, media-internals, webrtc-internals, histograms,
process-internals and network-errors loaded on the tested Electron 46 runtime;
Chrome-only pages and tracing were unsuitable.

## Measurements and checks

The measured settings replica work established semantic token/root-size/zoom
propagation, shared control geometry, fonts and native icons. Values belong in
`integration/discord-ui/components.css` and presentation data, not copies of
scaled screenshot pixels. Use `primitives.jsx` and `settings-controls.js` for
controlled values, callbacks and disabled state after rerenders. Keep focus,
selection and scroll stable during saves/filtering. Extend shared components.

`check_launch_settings.py` rebuilds Preact and exercises real controls, keyboard,
narrow layouts, zoom, long labels, theme tokens and reduced motion. Inspect the
rendered result for layout changes. Unit tests and synthetic sidebar definitions
do not prove exact live Discord visual parity. Native picker/settings entry
changes also need `tests/discord-ui/native-settings-check.cjs` through the native
audio fixture, including supported mod wrappers.

The real plain/Equicord layout check verified an ordinary row before Log Out,
Discord's own cog, immutable utility lists and correct native audio controls.
Evidence: `out/shared-discord-ui-smoke/`, `out/settings-equicord-repro/` and
`out/dpe-openasar-settings-check-3/settings.json`. These were signed-out fixtures;
a logged-in sidebar and physical desktop tray were not visually qualified.
