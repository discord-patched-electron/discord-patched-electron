# Client and Electron updater

Packages contain a small Python standard-library loader, download pins and the
trusted release key. OpenAsar, the Python updater and its bundled libraries, ASAR
patches, launcher logic and Linux integrations live in a separate `application.zip`.
Patched Electron downloads separately from the project's retained runtime cache. Discord's proprietary modules download directly from Discord
into the user's profile. The app never downloads Discord's stock Electron.

See [installation](../install.md) for user instructions.

## What can update

The package pins its initial application archive and file-index hashes in
`bootstrap.json`. That archive contains exact module pins in `client-update.json`
and the runtime identity in `runtime-update.json`. Installing a test package
explicitly selects its candidate; no approval lookup is needed for its exact pins.

Automatic application, client and Electron updates require a signed project release and a
matching manual approval record in `main/config/approved-release.json`. The tag,
package version, manifest hash and named approver must agree. A signature or
GitHub's **Latest** label alone is insufficient. Module changes under the same
Discord version need approval too.

The updater checks on launch and every 15 minutes. It prepares downloads in the
background and offers Discord's normal restart notification. Activation happens
at startup. Failed checks retain the package baseline or a previously approved
installation. Discord's hosted web interface updates independently.

Package updates supply the stable loader, trust key, desktop entries and system
dependencies. Application updates supply all other executable integration code,
including OpenAsar, ASAR patching and the Python updater.

## Application downloads and activation

`integration/bootstrap/loader.py` handles installed startup; the separate
`uninstall.py` removes system capture authorization during package removal. The loader downloads
the package-pinned application on first launch, rejects unsafe ZIP entries and
verifies every extracted file against a checksum-pinned index. It performs no
ASAR patching or Electron/client update work. Normal cached launches need no
network access before handing over to the application.

Initial downloads retry transient connection failures, incomplete responses and
temporary HTTP errors up to three attempts, discarding each partial file. Closing
setup before its first successful launch does not require downloading the verified
application again. If no working fallback exists, the package-pinned cache can be
retried offline. Malformed activation state is retained for diagnosis and rebuilt
from the verified package baseline; invalid application files still require repair.

Application generations live under `<profile>/dpe-application/<installation-id>/`.
The application checks for signed, approved releases in the OpenAsar startup
screen and during background checks. It stages a new generation through the
installed loader, retaining the signature and approval evidence. The loader
revalidates that evidence and the file index before executing any new code.
A bootstrap API mismatch requires a package upgrade instead. Changes to required
system dependencies must also increment that API, so old packages keep their
working application until those dependencies are installed.

Background preparation leaves running code unchanged and offers the normal
restart notification. At startup, the launcher hands off to the new application
before preparing its Electron or Discord modules. The client/runtime updaters
cannot advance past the selected application release. The launcher writes a
`started-<token>` marker immediately before its final exec into Discord, after
Electron and module setup. A trial becomes current after the matching Discord
HTTPS window finishes loading and writes `ready-<token>`. If setup exits before
the started marker, the update stays pending and is retried on the next launch
using verified cached code. If Discord starts but exits before confirmation, the
next launch falls back to the previous generation and rejects that payload.
Exited processes awaiting reaping do not count as running instances. Second
launches retain the running generation so they cannot activate an update
underneath it.
The loader retains PID/start-time records for all live instances, including the
experimental multi-instance mode, and prunes exited or reused PIDs on launch.

Package upgrades retain a newer approved application. A changed package pin
allows one fresh trial of that payload even if it was previously rejected;
an unchanged pin does not repeatedly revive a failed trial while a working
fallback exists. State from older loaders receives this allowance once for the
installed pin. Older rejections of other payloads remain rejected because their
state does not distinguish setup interruptions from client crashes. This change
requires an updated bootstrap package as well as the application launcher.
The archive hash identifies the payload; a changed package installation path
uses a separate application cache.
Offline mode uses verified cached code. `DPE_APPLICATION_UPDATES=0` disables new
application checks; cached pending updates can still activate on restart.
The loader and downloaded code never write to the installed package.

## Client downloads and approval

`<profile>/dpe-updater/` contains verified downloads, prepared generations and the
`current.json` pointer. A lock serializes updates. The updater:

1. Selects the package pins or the exact client pins from an approved release.
2. Verifies each archive before extraction. Traversal, external links, device
   files, duplicate entries and oversized archives are rejected.
   Relative links must remain internal after the staged module is renamed into
   its cache location; they cannot depend on the temporary directory's name.
3. Applies the voice/audio, Krisp and camera compatibility changes. Unknown
   layouts stop preparation. The desktop safe-storage adapter runs in memory.
4. Checks the prepared generation and publishes it through the installation
   transaction described below.

Module hashes allow reuse across host versions. A compatibility-code change
prepares a new copy; it does not rewrite modules used by a running client.
The updater never queries Discord's live latest manifest, `full.url` or host deltas.

Approval evidence stays in `dpe-updater/approvals/<manifest-hash>/`. Cached
launches recheck its signature and module pins. Offline mode and Quickstart need
pins matching the package or a retained approved release. Older unapproved caches
require an online launch to repair them; their files remain available to running
processes. Legacy release manifests without client pins cannot authorize updates.
If the current pointer is missing, damaged or no longer authorized after rollback,
offline selection can recover an intact generation derived from the selected
application's authorized sources and current compatibility fingerprint. It never
downloads or installs modules on that path.

## Patched Electron updates

`integration/updater/runtime.py` fetches the channel's small approval record from
`raw.githubusercontent.com`, then downloads its exact signed release assets.
It makes no GitHub REST API requests. A manually approved app release can contain alpha/beta Electron.
Checks cover channel, track, version ordering and minimum glibc.

The retained `github.json` file is now an explicitly marked `manual-approval`
compatibility descriptor for Bootstrap API 1. It is written only after signature
and approval validation; its legacy eligibility fields are not a fetched GitHub
API response. Existing cached API-era proof remains readable and revalidated.
This avoids the shared anonymous API quota and the race between approval and
GitHub's Latest-label promotion, without requiring a token or bootstrap upgrade.

Shared downloads publish complete files atomically and retry a transient transport
failure once. Partial, oversized or invalid responses never replace a complete
file. Server rate-limit/reset and Retry-After cooldowns survive subsequent calls
within the profile. The startup error page stops automatic retries for throttling
instead of requesting the same URL every ten seconds. Verified installed versions
remain usable when an optional update check fails.

The verification chain is:

```text
packaged public key → SHA256SUMS.sig → SHA256SUMS
                    → release.json → runtime.archive_sha256
```

The runtime ZIP comes from `runtime-cache-<runtime_key>/runtime-dist.zip` in the
project's cache repository. Extraction rejects unsafe entries and discards
Electron's demo resources. A Node-mode probe verifies the executable's version
before selection. Failed checks keep a verified current runtime. A damaged cache
is repaired from the pinned archive; an offline launch requires an intact cache.
Cached Electron must also be executable. Lost execute permission triggers repair
from a supplied archive or the normal download path; offline mode without usable
replacement bytes reports that no verified runtime is available.

The package's `runtime-update.json` authorizes its exact baseline without a
release lookup, including manually installed test candidates. Other automatic
runtime updates still require the approval chain above. First launch can fetch
the baseline when GitHub's latest-release check fails or updates are disabled.

Runtime generations live under `dpe-runtime/<application-path-id>/generations/`.
Installation IDs separate portable copies and system apps. Package files remain
unchanged. The archive hash controls reuse, even when a newer package release
catches up with an automatic runtime update. A package that pins a newer runtime
prepares it on launch, retaining a verified older runtime if preparation fails.
Application updates and relocated portable copies reuse the verified runtime
bytes from another cache generation without a download. Each gets its own
resource link, so an update cannot redirect a running installation.
On Ubuntu, `aa-exec` applies the installed AppArmor profile to downloaded runtimes.
Chromium's sandbox stays enabled.

The selected Electron is used for desktop-module preflight. Relaunch and autostart
return through the package launcher. See [launcher settings](../linux-launcher.md#troubleshooting)
for offline and runtime overrides.

## Shared profile and installation recovery

Canary uses `~/.config/discordcanary`, respecting `XDG_CONFIG_HOME`.
`DISCORD_USER_DATA_DIR` selects a parent folder; `DPE_PROFILE_DIR` selects an
exact profile path.

`installation.py` publishes a real `app-<version>/` directory that official mod
installers can discover. Runtime and resource files are writable copies or
reflinks. The `modules/` link points to the prepared installation; a running
process resolves it once, so later activation cannot redirect its module lookups.

Discord's native `installer.db` schema is preserved, including `install_id`,
unrelated keys and older host records. A private lock and SQLite transaction
coordinate publication. A durable journal records replaced directories, and an
activation token distinguishes committed updates from interrupted ones. Files
and directory changes are flushed before the database commits.

On failure, recovery restores uncommitted replacements. Displaced stock files,
database snapshots and interrupted trees remain under `dpe-updater/backups/`.
Running the package launcher again reconciles interrupted activation. Old symlink
installations migrate automatically. Cache and backup cleanup is still manual;
close Discord before removing old generations.

The channel executable routes back through the package launcher. If Discord's
stock updater replaces shared files, the next fork launch detects the changes
and restores verified copies. Package and download caches are kept separate
from the writable installation.

## Mods and OpenAsar settings

The updater preserves official Vencord and Equicord loaders while refreshing
`_app.asar` with the new bootstrap. Mod payloads and settings stay outside the
activation transaction, so a mod can update itself without being rolled back by
a failed client update. See [mod preservation](client-updater.md#mod-preservation-and-installer-network-failures).

OpenAsar settings, custom CSS/JS, theme sync and Focus presets are retained.
Explicit launcher flags win over presets. Quickstart validates a prepared client
offline, then checks updates in the background. OpenAsar's self-updater is replaced
so it cannot overwrite the integration.

The pinned settings page, preload and Monaco editor run locally. Settings IPC
accepts only the local main frame, and window navigation is blocked.

## Startup screen and dependencies

After the installed loader selects the application, its `bootstrap.py` selects a
verified cached runtime before opening the 300×300
startup screen. A first install uses a cancellable native progress dialog while
Python downloads Electron; the helper can also run without a desktop dialog.
The GTK3 dialog is titled **Discord Patched Electron** and says
**Preparing Electron...**. Native widgets use the desktop
theme, with centered content, 24px outer padding and 8px between the message and
progress bar. The titlebar close button cancels preparation. It uses Python's
`ctypes` and the package's existing GTK3 dependency, without Tkinter or PyGObject.
GTK's animation setting controls the pulse. Zenity remains a fallback for portable
installs without GTK3 and is still needed for the screen-share audio picker.
Later launches reuse the cache without a network check before the splash.
`startup.cjs` manages the window and retries; `runner.cjs` manages the cancellable
helper group and bounded JSON-lines output. Preparation begins at DOM readiness
without waiting for optional media downloads. Diagnostics go to stderr.
Both helper paths terminate remaining members of their process group on completion,
failure or cancellation, including descendants that outlive the parent process.
Native-dialog cancellation is checked again before accepting a completed setup.

Every startup stage uses the launcher's `CHROME_DESKTOP` identity, matching the
installed desktop entry and icon. GTK sets its program name before initialization
and its X11 class afterward; the packaged logo is loaded at 128px to fit X11's
window-icon limit. Zenity receives the same identity as `argv[0]`, including for
bootstrap errors. Electron sets its desktop name before readiness and supplies
the packaged icon to every window, including settings and client popouts.
OpenAsar autostart entries use the matching icon name and `StartupWMClass`.

`modern-splash.html` uses OpenAsar's sandboxed preload and a single progress bar.
Module and runtime progress share task identities so concurrent work cannot move
the bar backward. The pinned upstream HTML and settings sources stay unchanged.
Fonts and animations download from Discord's CDN, are verified against
`splash-media.json`, and remain in `discord_asset_cache/openasar/`. They are not
packaged. Cached media works offline; missing media does not prevent text display.

Theme sync uses cached Discord/custom colors and is enabled by default.
`openasar.themeSync: false` disables it. `BACKGROUND_COLOR` supplies the fallback
before a theme cache exists. Reduced-motion preferences stop animation playback.

Failures show recovery guidance, **Retry now**, **Pause retries** and **Copy details**.
Connection failures retry after ten seconds. Setup, permission, space and
verification errors pause for user action. Copying uses scoped IPC and only the
main process's current error. Closing the window through the desktop cancels
preparation; there is no splash close icon or animation button.

Python **3.11 or newer**, Brotli and GnuPG are system dependencies. Capstone and
pyelftools travel in the application payload from checksum-pinned wheels in
`uv.lock`, with their sources, native library and notices. Helpers use the
application's `python/` directory and
disable user site packages and bytecode writes. Preparation prerequisites are
checked when needed, so missing patch tools do not block a usable cached client.

## Validation

Run `bun run test` and `bun run lint`. Regression coverage includes approval,
archive boundaries, module reuse, damaged-cache repair, activation rollback,
offline startup, mod preservation and helper cancellation. GPG fixtures verify
real signatures. Brotli is required for archive tests.

Release launch checks set `DPE_CLIENT_PINNED=1` and `DPE_RUNTIME_BUNDLED=1`.
The historical `DPE_RUNTIME_BUNDLED` name now selects exact package runtime pins;
local qualification apps can still provide a bundled verified runtime.
`runtime.py --archive <runtime-dist.zip>` seeds a fresh test profile using the
same checksum, extraction and executable checks, without fetching Electron.
Reports compare prepared module URLs, revisions and hashes with the packaged
pins. Packaging rejects untested or changed pins, proprietary client payloads,
media assets, generated bytecode and bundled Electron. The same pins enter signed `release.json`.

## Mod preservation and installer network failures

Official Vencord/Equicord installers must discover a real writable `app-<version>`
directory. Preserve their public `app.asar` loader, refresh `_app.asar` to the
current project bootstrap, and keep payload/settings outside the rollback
transaction. Uninstall must restore the current bootstrap, not a cached old one.
Check install, self-update, package upgrade, relocation, relaunch and uninstall.
Never rewrite an installed mod payload just to repair a DPE update.

Installer downloads use bounded retries and verified asset digests. A GitHub API
quota failure must remain distinct from unavailable releases or corrupt bytes;
never remove checksum/approval gates or repeatedly prompt on startup. Settings
IPC is owner/main-frame scoped, and opening/installing mods is explicit user work.

## Recovery evidence and limits

Offline regressions cover interrupted publication, stale pointers, application
rollback, non-executable cached runtimes, multi-instance survivor tracking, native
database transactions and mod preservation. Tests use real signatures and
failure injection; they do not prove physical power-loss recovery.

[Minimal-bootstrap evidence](../evidence/) and local `out/minimal-bootstrap-check/`,
`out/bootstrap-reliability/` and `out/runtime-split-check/` recorded matching
installer payloads, fresh login/relaunch and a signed app upgrade that preserved
original code and reused Electron. The old runtime-split packages were about
5.3–6.4 MB; later minimal bootstraps were about 0.88 MB with an approximately
5.5 MB separate application archive. These are different packaging stages.
Ubuntu/Fedora containers did not qualify a full AppArmor desktop or live call.

Final 25 October updater checks used real native-updater database discovery,
read-only package files, signed runtime downloads and SIGTERM-resistant helper
descendants. Useful local evidence: `out/modern-native-db-test/`,
`out/github-runtime-upgrade-launch/`, `out/github-runtime-readonly.log`,
`out/updater-review-retained-recovery.log`, `out/pr1-native-startup-report.json`
and `out/pr1-updater-states-v2/{interaction,motion}-report.json`. Older `out/`
paths may have been cleaned; Git preserves the original reports' context.

For temporary profiles, ENOSPC and EDQUOT are different failures. `/tmp` may be
quota-limited even when the SSD has space; very long TMPDIR paths can also exceed
Chromium's Unix socket limit. Use a short disk-backed disposable path and retain
explicit diagnostics rather than weakening atomic writes.
