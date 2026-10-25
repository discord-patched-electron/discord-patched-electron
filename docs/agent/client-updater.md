# Module-only Linux client

The Arch [Discord recipe](https://gitlab.archlinux.org/archlinux/packaging/packages/discord/-/blob/main/PKGBUILD)
now installs a launcher and `updater_bootstrap`. That installer downloads a host
archive containing both Discord's bootstrap ASAR and stock Electron, plus separate
module distributions. Inspection of the October 25, 2026 manifest and native
updater found no resource-only host download option.

This branch therefore uses pinned OpenAsar startup code with a project-owned
module updater. It consumes the new `updates.discord.com/distributions/app/manifests/latest`
API, validates module URLs and SHA-256 hashes, and never fetches `full.url` or host
deltas. OpenAsar's upstream native updater, legacy downloads and self-update are
replaced. Saved settings, renderer customization and performance presets are
preserved; explicit launcher flags take precedence. The splash and settings use OpenAsar's original screens served locally,
as described below. All executed bootstrap source and its AGPL license ship
in the package. `resources/app.asar` contains only the project's entry point and
metadata, preserving the layout used by official mod installers.

## Ownership and updates

- The package provides the initial patched Electron, bootstrap source and Linux integrations.
- `<profile>/dpe-updater` owns downloaded modules, complete generations
  and the atomic `current.json` pointer. No install hook downloads or launches Discord.
- A lock serializes updates. Archives are checksum-verified before extraction;
  extraction rejects traversal, external links and device files. Failed downloads
  or patch validation leave the current generation unchanged.
- Module content hashes allow reuse across host versions. Compatibility code
  changes create new prepared modules. Old generations are retained so running
  clients are never rewritten underneath Electron.
- `installation.py` publishes prepared files through the shared `app-<version>/`
  tree and `installer.db`. See the compatibility follow-up below for its native
  schema, rollback and backup behavior.
- Voice/audio and Krisp patches apply locally before activation, including the
  validated camera function scope. The safe-storage adapter runs in memory when
  the downloaded desktop ASAR is loaded. Unknown patch layouts fail closed.
- The replacement updater implements the desktop module's event contract, checks
  every 15 minutes and restarts through the package launcher. Native-updater
  migration is disabled to prevent a later switch to stock Electron.

## Patched Electron updates

`integration/updater/runtime.py` checks the project's GitHub `releases/latest`
endpoint. Only public, non-prerelease app releases qualify; under the release
workflow this is the manually approved release. The Electron track itself can
still be alpha/beta. It checks channel, track, version ordering and minimum glibc.

The trust chain is the packaged public key → `SHA256SUMS.sig` → `SHA256SUMS` →
`release.json` → `runtime.archive_sha256`. The archive comes from the immutable
`runtime-cache-<runtime_key>/runtime-dist.zip` release in the project's public
cache repository. No full portable package or Discord host archive is fetched.
The existing publication workflow already signs this metadata; no additional
release artifact or GitHub token is needed.

The updater extracts into a temporary directory, rejects unsafe ZIP entries,
discards Electron's default demo resources, and tests the executable in Node mode
against the signed version before atomically selecting it. Failed checks retain
the current runtime. The package runtime remains available as a fallback if the
cache is damaged or `DPE_RUNTIME_BUNDLED=1` is set. This does not disable Chromium's
sandbox. On Ubuntu, the launcher uses `aa-exec` to enter the installed AppArmor
profile when running the downloaded executable.

Runtime generations live under `dpe-runtime/<installation-id>/generations/` in
the Discord profile. Installation IDs separate portable copies and system apps.
Their `resources` link points to the installed package, preserving OpenAsar and
the official mod installer's loader. Package-manager files are never rewritten.
An updated system package supersedes an older cached runtime automatically.
GitHub checks, including failures, are limited to once per 15 minutes. Offline
mode and the runtime-only controls are documented in [installation](../install.md).

The background check prepares both update types and emits the normal Discord
restart notification if either changes. Desktop module preflight uses the
selected Electron. Relaunches and autostart always go through the stable package
launcher. Bootstrap/integration code and signing-key changes still use package
updates; the automatic GitHub path updates Electron itself.

The portable, nFPM and generated Arch paths all stage the same client-free layout.
Packaging rejects extra ASARs, native Discord modules, distro archives and old
bundled client directories. Published AUR recipes use the verified portable tree.
Pinned client locks remain inputs for isolated native regression tests; normal
launches resolve the current module manifest. Release launch reports record the
actual tested version and generation.

## OpenAsar startup screen

`integration/updater/openasar/` pins the exact upstream `splash/index.html` from
OpenAsar/cdn commit `4448ff4b5d7442802fa912054699b9c1e2ab7dd8`, with its MIT license.
Do not format or redesign it. `window.cjs` retains OpenAsar's 300×350 frameless
Linux window and loads the original preload from the pinned bootstrap. A narrow
local protocol serves the HTML with a restrictive CSP. The original animation
and Whitney font download separately from the pinned revision, are verified by
size and SHA-256, and stay under `discord_asset_cache/openasar/` in the user's profile.
Packages contain neither media asset; offline launches reuse verified cached
copies. Without cached media and network access, text still renders.

The launcher starts this screen with its bundled patched Electron before running
Python. `startup.cjs` owns lifecycle and OpenAsar's ten-second retry behavior;
`runner.cjs` owns the cancellable Python process group and bounded JSON-lines
output. `splash-state.cjs` adapts byte/module progress to the original renderer's
state contract. Detailed errors remain in stderr. Closing the screen cancels
preparation and returns failure to the launcher. No incomplete first installation
can be skipped. After success, the launcher resolves the prepared pointers
offline, applies its camera/AppArmor setup and starts the selected fork runtime.
The normal OpenAsar bootstrap uses the same screen until Discord's page is ready.
Neither path loads executable splash code from the network.

Capstone and pyelftools are staged from checksum-pinned wheels selected from
`uv.lock`, including their upstream notices and Capstone's native library. Both
foreground and background helpers set `PYTHONPATH` to the package's `python/`
directory and disable user site packages and bytecode writes. Python itself and
Brotli remain distro dependencies. Prerequisites are checked before downloads.
The old portable-launch check inherited uv's environment and therefore missed
the user's system-Python import failure; it now launches with the system PATH
and without virtualenv/Python overrides.

## Validation

Local testing reuses the verified retained Electron 46.0.0-alpha.1 runtime from
`out/cache-migration-check`; no Electron compilation was started. Canary 1.0.2366
reached the native login form on Wayland and relaunched with a fresh test profile.
No account login, call, message, webcam capture or screenshot was performed.

Local evidence from October 25, 2026:

- `out/module-only-final-launch/`: native Wayland login and relaunch with the app
  in a path containing spaces; client 1.0.2366, generation
  `d8a6a9d1ee2320a4b9c5956788651fbe7c030c032ebc8dc86935c289f3fc9ab9`.
- `out/module-only-final-mods/`: official Vencord 1.4.2 and Equilotl 2.3.0
  installers passed install, simulated package upgrade, reinstall, login,
  relaunch and uninstall. Both restored the project's loader exactly.
- `out/module-only-final-packages/package-checks.json`: all four formats have
  identical app payloads and no bundled Discord client data.
- `out/module-only-arch-container2/packaging-report.json`: the generated Arch
  recipe passed in the repository's container with normal dependency checking.
- `out/module-only-ubuntu-install.log` and `out/module-only-fedora-install.log`:
  actual Debian/RPM installation, new Python dependencies and ordinary-user
  file access/runtime loading passed in Ubuntu 24.04 and Fedora 44 containers.

`bun run test` passed 147 Python and 25 JavaScript tests; `bun run lint` passed.

The subsequent GitHub runtime update implementation passed 156 Python and 26
JavaScript tests plus lint. Its additional evidence is:

- `out/github-runtime-upgrade-launch/`: an isolated copy with simulated older
  baseline metadata fetched the real approved GitHub release, verified its
  signature chain, downloaded the retained Electron ZIP, and reached the native
  Wayland login form twice. Both reports identify the executing binary under
  `profile/dpe-runtime/`, rather than the packaged executable. This reused the
  published Electron 46.0.0-alpha.1 build; no compiler was started.
- `out/github-runtime-launch/`: the normal baseline correctly kept its already
  current runtime and passed login/relaunch.
- `out/github-runtime-mods/`: both official mod installers passed launch/relaunch
  with the downloaded runtime and restored the original loader on removal.
- `out/github-runtime-readonly.log`: offline launch with read-only package files
  passed using the downloaded runtime; all package file hashes stayed unchanged.
- `out/github-runtime-packages/package-checks.json` and
  `out/github-runtime-arch-smoke/packaging-report.json`: release formats and the
  generated Arch recipe passed with the pinned key and runtime update policy.
- `out/github-runtime-ubuntu-install.log` and
  `out/github-runtime-fedora-install.log`: packages installed, GnuPG tools were
  available and ordinary-user runtime loading passed. Ubuntu also supplied
  `aa-exec`. These containers do not test a live AppArmor-restricted GUI session.

The retained local packages are unsigned development outputs, not published
releases. The compiler runtime was reused unchanged. Desktop compatibility
transforms are checked using its Node mode before activating each new client.

Run `bun run test` and `bun run lint`. Updater regression tests cover host-download
exclusion, source validation, module reuse, failed-update rollback, offline startup,
archive boundaries and package payload rejection. Brotli is needed for archive
tests. Real desktop and package checks remain necessary after bootstrap changes.

Automatic updates can introduce an incompatible upstream desktop module between
runtime releases. The compatibility patches deliberately reject unknown layouts.
Old runtime generations and cached modules currently require manual cleanup after
closing Discord; automatic garbage collection is not implemented.

## Architecture and quality review

The October 25 review covered all 41 files changed by the initial module-only
bootstrap and GitHub runtime updater commits. It kept the two update sources
independent and addressed these findings:

- Shared HTTPS downloads, checksums and atomic metadata writes now live in
  `integration/updater/support.py`; Electron no longer imports the Discord client
  updater. The coordinator passes its selected executable explicitly. Subprocess
  diagnostics go to stderr so stdout remains a machine-readable result.
- Malformed cache pointers and check timestamps no longer permanently block
  online recovery. A complete runtime left by interruption between directory
  rename and pointer publication is verified against signed metadata and reused.
  Failed writes remove temporary metadata files. Missing essential runtime files
  cause fallback to the packaged runtime.
- Discord rejects module revision downgrades within the same host version and
  validates complete generations before selecting them. Truncated HTTP responses
  retain an existing usable installation. Extraction limits include tar metadata
  and trailing decompressed bytes, with an entry limit and duplicate-file rejection.
- Bootstrap transforms are centralized with the desktop transforms. Missing or
  duplicate OpenAsar patch sites fail explicitly, including updater configuration
  and autostart. Callers cannot mutate installed module versions through snapshots.
- Staging, release archives and the generated Arch smoke check share payload
  validation. Launches disable Python bytecode writes into the app; packaging
  rejects those generated caches, extra client directories and unexpected links.

Review validation used the retained Electron 46.0.0-alpha.1 compiler artifact:

- `out/updater-review-tests.log`: 164 Python and 28 JavaScript tests passed.
  `out/updater-review-lint.log`: all required lint checks passed.
- `out/updater-review-launch/`: fresh Canary 1.0.2366 Wayland login/relaunch passed,
  with no generated bytecode in the app.
- `out/updater-review-packages/package-checks.json`: all four release formats
  passed with identical app payloads, no bundled Discord client and no bytecode.
  `out/updater-review-arch/packaging-report.json`: the generated recipe also passed.
- `out/updater-review-retained-recovery.log`: a simulated interrupted activation
  reused the real retained runtime without downloading its ZIP. GitHub's anonymous
  API was rate-limited, so this check supplied the earlier saved API response and
  fetched the signed release assets directly; signature verification remained real.
  `out/updater-review-cached-launch/` then passed offline login/relaunch using that
  recovered executable. An earlier fixture attempt used a relative profile path
  and was corrected to an absolute path before this successful check.

No Electron rebuild, publication, account login, call or webcam capture was performed.
This pass does not add cache garbage collection or prove compatibility with future
Discord modules. Existing live-call and AppArmor GUI coverage limits still apply.

## Original splash and portable dependencies: October 25 follow-up

The initial command-line-only startup missed the requested screen, and tests
inherited uv's Capstone/pyelftools instead of exercising ordinary system Python.
The changes above correct both. An intermediate custom splash was removed;
the packaged HTML is byte-for-byte identical to the pinned OpenAsar source.

- `out/openasar-ui-tests.log` and `out/openasar-ui-lint.log`: 166 Python and
  34 JavaScript tests, plus lint, pass. New coverage exercises streamed results,
  missing Python, cancellation, pinned media and offline cache verification,
  upstream HTML integrity, state translation and wheel staging. Distribution
  checks reject the proprietary animation and font as well as Discord ASARs.
- `out/openasar-ui-clean-launch/` and `out/openasar-ui-offline/`: fresh system-Python
  launch, relaunch and cached offline launch reach Canary 1.0.2366's login form
  on native Wayland. Both first-launch reports observed the updater screen.
- `out/openasar-ui-failure-final/report.json`: the real Electron window is 300×350,
  plays the upstream video, loads Whitney, exposes the original preload bridge
  and has no renderer `require`. An incomplete offline first launch counted down
  through 8/6/4/2 seconds and retried at 9; Quit exited with status 1 without
  publishing a client pointer. The first fixture attempt closed the CDP target
  before its evaluation returned; delaying Quit by 100 ms fixed the test race.
- `out/openasar-ui-mods/`: official Vencord and Equilotl install, login/relaunch
  and removal pass. Each restores the original loader exactly.
- `out/openasar-ui-packages/package-checks.json`: all four package formats have
  identical client-free payloads. `out/openasar-ui-arch/packaging-report.json`
  verifies the generated Arch recipe with pinned Python wheel sources.
- `out/openasar-ui-ubuntu-install.log`: the Debian package installs in Ubuntu
  24.04, remains readable as an ordinary user and loads the retained runtime.
  System Python imports bundled Capstone 5.0.6 and pyelftools 0.32.

The final local build is `out/openasar updater final app`; its launch report is
`out/openasar-ui-final-launch/`. It also cancels the helper group on application
quit or SIGINT/SIGTERM. It reuses the retained verified
Electron artifact. No runtime compilation, publication or authenticated Discord
interaction was performed. The browser preview used only the public upstream
splash and a progress fixture; no desktop or camera images were captured.

## Settings and native install-tree compatibility: October 25 follow-up

The live `https://cdn.openasar.dev/splash/` response was HTTP 200 and SHA-256
`470e6583752489cf4da14d1e96e063b249a4883ab0813d8c8035b87ea5252ebe`, exactly our
pinned HTML. OpenAsar and CDN HEADs still match their locks. The retained current
Canary 1.0.2366 bootstrap was also inspected: Discord itself uses a newer 300×300
splash, gg sans and `connecting.webp`. OpenAsar still serves its older screen,
so its HTML, animation, typography and dimensions remain unchanged here.
Downloaded proprietary reference code stays in `out/`.

The adapter previously replaced the entire `openasar` settings object. It now
preserves saved preferences, custom CSS/JS, tracking/typing controls, DOM optimizer,
multi-instance, hardware acceleration and Focus presets. Explicit launcher flags
win over presets. Quickstart validates the prepared client offline, falls back to
normal preparation if incomplete, and checks updates in the background. Bootstrap
self-replacement remains disabled because it would remove this integration.

The unchanged upstream settings HTML/preload and pinned Monaco editor run locally.
Settings IPC accepts only the local main frame, and window navigation is blocked.
The settings entry in Discord is the original OpenAsar renderer injection.
CSS serialization also escapes template interpolation and replacement tokens.
The theme collector accepts both legacy custom-theme tokens and current Discord
tokens, including light/default themes. It keeps OpenAsar's five-color
`userDataCache.json` contract and respects `openasar.themeSync === false` in both
windows. Splash media now uses `discord_asset_cache/openasar/`.

`DISCORD_USER_DATA_DIR` has Discord's parent-directory semantics; Canary adds
`discordcanary/`. `DPE_PROFILE_DIR` is a separate exact-path override. The path
adapter supplies the standard profile, version subdirectory, `module_data`, logs,
asset cache, install root and `app-<version>/resources` locations. Local reference
builds also honor the exact override, so test launches cannot leak into a real profile.

The user explicitly chose to share the native installation tree and database.
`installation.py` publishes `app-<version>` as an atomic symlink to a prepared
payload, with modules at `modules/<name>-<revision>/<name>/`. Payloads are writable
reflinks/copies rather than links into the package or verified download caches.
A private lock serializes publishers; a SQLite write transaction coordinates
database changes. The native `key_values` table contains the real host/module
versions, `Installed` states and SHA-256 file manifests. Existing `install_id`,
unrelated keys and older host records survive. Stock files/symlinks displaced by
takeover and a database snapshot are retained under `dpe-updater/backups/`.
Caught activation failures restore the previous links and database; interrupted
activation can be reconciled by running the package launcher again. Running
clients keep their original resolved module paths during background publication.

The channel executable is a wrapper back through the stable package launcher.
This lets Discord's stock shell launcher use the same installation without losing
runtime selection, mod loading or camera setup. Native updater writes can replace
shared files; subsequent fork launches detect changed installed bytes and publish
a fresh copy. Old payloads remain available. The camera helper has a dedicated
`preload/` library directory so paths containing spaces work without exposing
Electron's other libraries to system child programs.

Validation (fresh profiles, no account login or calls):

- `bun run test`: 172 Python and 41 JavaScript tests; `bun run lint`: pass.
  New cases cover native schema/preservation, takeover rollback, concurrent
  publishers, corrupted-file repair, runtime/mod refresh, anchored module paths,
  saved preferences, theme sync, safe CSS serialization and local editor assets.
- `out/openasar-compatible-launch/`: fresh Canary 1.0.2366 login on native Wayland,
  using the retained compiled fork and system Python with bundled dependencies.
- `out/openasar-compatible-settings/` and `out/openasar-compatible-no-sync/`:
  native splash/settings colors, unchanged 300×350/500×650 windows, offline Monaco,
  CSS editing/saving and preservation of other cached fields. ThemeSync false
  keeps the upstream default colors while custom client CSS continues to work.
- `out/openasar-native-db-symlink-test/`: the real Discord `updater_bootstrap`
  accepted the database and symlinked host tree, printing `app-1.0.2366`. Its local
  manifest fixture forbade payload downloads; only two manifest requests occurred.
- `out/openasar-parent-launch-check/` and `out/openasar-official-launch-check/`:
  parent-directory override, spaces in the profile path, and the official shell
  launcher all reach login through the canonical channel entry. The camera helper
  also resolves in Electron Node mode and a system child executable from that path.
- `out/openasar-compatible-packaging/packaging-report.json`: generated Arch
  package passes with the pinned CDN/editor source and client-free payload rules.

The current runnable build is `out/openasar-compatible-app`. No compiler was
restarted. Account-specific settings-sidebar rendering and real calls were not
tested; existing live-call/AppArmor coverage limits still apply.

## Modern splash and default installer discovery

This follow-up supersedes the symlink publication and 300×350 splash described
above. The public `app-<version>/` is now a real directory: both upstream mod
installers enumerate directories without following symlinks. Its runtime and
resources are independent copies/reflinks. `modules/` links to the prepared
installation, and the running module API resolves that target once so a later
activation cannot redirect a running process's module lookups.

The native `installer.db` schema and profile paths remain shared with Discord.
A background check only prepares updates; activation happens during startup.
A durable activation journal records the old directories before any rename.
The database's activation token distinguishes a committed update from an
interrupted one. Recovery restores uncommitted replacements and retains the old
files, database snapshot and interrupted trees under `dpe-updater/backups/`.
Payloads and directory changes are flushed before committing the database.
Existing profiles using the previous symlink layout migrate on their next launch.

The public `resources/app.asar` is writable by the user's normal installer.
When `_app.asar` is present, updates preserve the installed mod loader and refresh
its underlying project bootstrap. Uninstalling the mod does not resurrect an old
loader from the private cache. Package-side legacy mod wrappers remain supported.

`modern-splash.html` is a local adapter using OpenAsar's existing sandboxed
`Splash.onState` preload. The pinned upstream screen/settings sources are unchanged.
The adapter follows the inspected Canary 1.0.2366 splash measurements: 300×300,
200×200 animation, gg sans 500 at 16px, sentence-case status, 180×8 track with
4px corners, native spacing and grey fill. At the user's request there is one
bar. It combines completed download and install work, keeping parallel callbacks
monotonic; text identifies Electron or the current module count. Python events
now use the same task identity across all phases. Retry countdown remains 10s.

Font URLs and light/dark spinner URLs are pinned by SHA-256 and size in
`splash-media.json` and fetched directly from Discord's CDN into
`discord_asset_cache/openasar/`. The current web client's dark spinner is
byte-identical to the pinned OpenAsar spinner. No fonts, animation media,
proprietary bootstrap or modules are shipped. No stock host download is used.
The package validator rejects these media filenames, and the integration
fingerprint includes the new screen and asset metadata.

Theme sync is enabled by default, with explicit `openasar.themeSync: false`
respected. Cached Discord/custom theme colors include the progress fill.
`BACKGROUND_COLOR` provides a first-launch fallback before a theme cache exists;
light backgrounds get dark text and Discord's light spinner. Settings and custom
CSS continue through OpenAsar's existing settings/editor and renderer adapter.

Validation on native Wayland, using the retained Electron 46.0.0-alpha.1 runtime
and fresh test profiles, without account login, messages, calls or camera capture:

- `out/modern-first-launch/`: first-run module download and Canary 1.0.2367 login,
  system Python, bundled Capstone/pyelftools, public app-directory runtime.
- Official Vencord Installer v1.4.2 (`fe6e041`) and Equilotl v2.3.0 (`1a3d962`)
  both listed the fresh `~/.config/discordcanary` fixture automatically with no
  location argument. Vencord install and uninstall succeeded; Equicord install
  succeeded. `out/modern-vencord-launch/` and
  `out/modern-equicord-update-launch/` verified the injected native and renderer
  APIs at the login screen. The latter forced installation replacement.
- `out/modern-verified-relaunch/`: offline relaunch retained Equicord after another
  activation and reached login using the fork at `app-1.0.2367/electron`.
- `out/modern-splash-report.json` and `out/modern-splash-offline-report.json`:
  native Electron verified one bar, dimensions, loaded gg sans, loaded animation,
  default/custom/light colors, explicit theme-sync opt-out, and offline reuse.
- `out/modern-native-db-test/`: the actual native `updater_bootstrap` accepted
  the database/real app directory and printed `app-1.0.2367`. The loopback manifest
  fixture refused all downloads; only two manifest requests occurred.
- `bun run test`: 178 Python and 42 JavaScript tests passed. `bun run lint` passed.
  `out/modern-updater-packages/`: generated Arch package smoke test passed with
  its dummy runtime and client-free payload validation.
- Regression tests cover preparation without activation, legacy symlink migration,
  mod preservation/uninstall, interrupted and committed activation recovery,
  file repair, concurrent publishers, progress identity, and media exclusion.

The installer binaries search their own fixed paths. Profiles explicitly moved
with `XDG_CONFIG_HOME`, `DISCORD_USER_DATA_DIR` or `DPE_PROFILE_DIR` may require the
installer's Custom Location option, just as moved stock installations do.
Backups/prepared generations are retained; automatic retention cleanup is not
introduced by this change. Live calls and an actual power-loss reboot were not
performed; interruption recovery is exercised with failure injection.

## Real GitHub download fixture

`out/github-download-test/run.sh` launches a separate app/profile for manually
exercising the Electron download screen. The copy simulates an older release
using a zero baseline archive hash and `release_version: 20000101.000000`; its
actual Electron version, production updater code and signature checks are unchanged.
Original metadata is retained in the fixture. Verified prepared modules from a
fresh logged-out test profile are reflinked into the new profile to isolate the
runtime-download test. No account data or mod settings are copied.

`out/github-download-test/verification-report/` records a real download of the
approved 46.0.0-alpha.1 GitHub runtime, successful verification, selection and
native Wayland login. The temporary verification profile was removed afterward;
the user's `profile/` remains uncached for its first runtime download. Existing
app/runtime caches were untouched. The 220 tests and lint passed again.
