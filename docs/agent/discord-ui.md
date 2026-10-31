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

## Validation (31 October 2026)

`tests/discord-ui/settings.test.cjs` covers lazy and cached factories, another
mod's wrapper, translated labels, native icon identity, immutable lists, duplicate
prevention and missing/ambiguous icons. The existing audio discovery and lifecycle
tests now exercise the common runtime. Packaging tests load the staged bridge and
compare its shared files with the source files.

`tests/discord-ui/native-settings-check.cjs` runs inside the native audio smoke on a
fresh signed-out Canary profile. It registers the settings chunks without opening
account settings, initializes the layout definition and uses Discord's real
compiler to verify the row type, default variant, parent, icon, title and placement.
The audio smoke then verifies native controls, keyboard behavior, zoom/font scaling,
search, selection, teardown, broken components and host unmounts in the same page.

Evidence: `out/shared-discord-ui-smoke/final-results/native-settings.json`, `result.json`,
`layouts.json` and `scaling.json`. Retained Electron 46.0.0-alpha.1 was reused under
Xvfb. No account login, message, call or physical capture was performed. This checks
the real native layout contract; a logged-in sidebar was not visually inspected.

The separate OpenAsar configuration/diagnostics integration also passed with a
fresh profile: `out/dpe-openasar-shared-settings-check/settings.json`.

Final repository checks passed: `bun run lint`, 395 Python tests and 131 JavaScript
tests through `bun run test`.

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

The missing entry was reproduced in `out/settings-equicord-repro/before2.log`.
After the fix, `out/settings-equicord-repro/after2/native-settings.json` confirms
the label, ordinary native row type, exact Discord cog and placement; `result.json`
and `scaling.json` cover the native audio dialog in the same modded page. The plain
Discord check also passed in `out/settings-equicord-repro/plain/`. These are fresh,
signed-out profiles using retained Electron 46.0.0-alpha.1. No account interaction
or capture was performed.
