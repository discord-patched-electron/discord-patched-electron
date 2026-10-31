# Client and Electron updater

Packages contain a small Python standard-library loader, download pins and the
trusted release key. OpenAsar, the Python updater and its bundled libraries, ASAR
patches, launcher logic and Linux integrations live in a separate `application.zip`.
Patched Electron downloads separately from the project's retained runtime cache. Discord's proprietary modules download directly from Discord
into the user's profile. The app never downloads Discord's stock Electron.

This page describes the current design. See [installation](../install.md) for
user instructions and [updater history](updater-history.md) for earlier designs
and detailed test records.

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

`integration/bootstrap/loader.py` is the only installed Python code. It downloads
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
cannot advance past the selected application release. A trial becomes current
after the matching Discord HTTPS window finishes loading; a process that exits first causes
the next launch to fall back to the previous generation. Exited processes awaiting
reaping do not count as running instances. Second launches retain
the running generation so they cannot activate an update underneath it.
The loader retains PID/start-time records for all live instances, including the
experimental multi-instance mode, and prunes exited or reused PIDs on launch.

Package upgrades retain a newer approved application. The archive hash identifies
the payload; a changed package installation path uses a separate application cache.
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

`integration/updater/runtime.py` checks the project's GitHub `releases/latest`
endpoint. A public, approved app release can contain alpha/beta Electron.
Checks cover channel, track, version ordering and minimum glibc.

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
a failed client update. See [mod preservation](client-mods.md).

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

The [25 October test records](updater-history.md) include fresh-profile Wayland
login/relaunch, official mod installers, downloaded-runtime launch, installation
recovery, startup errors and all four package formats. Tests reused the verified
Electron 46.0.0-alpha.1 runtime. Ubuntu/Fedora container checks do not establish a
full AppArmor-restricted desktop session. Live calls and physical power-loss
recovery remain untested; interruption recovery uses failure injection.

### Runtime/package separation, 26 October

`out/runtime-split-check/` retains the local validation artifacts. The checks
reused the verified Electron 46.0.0-alpha.1 archive from the current runtime lock;
no Electron compilation was started.

- All four installer formats contain the same updater payload without a
  `runtime/` directory. Sizes are 5.3 MB (tarball), 6.3 MB (Debian), 6.4 MB (RPM)
  and 6.2 MB (Arch), measured in decimal units.
- Fresh-profile launch and relaunch passed for the qualification app and the
  extracted runtime-free tarball. The latter seeded Electron through the real
  updater's verified local-archive path before preparing Discord modules.
- A final fresh-profile check downloaded Electron from the public runtime cache,
  reached Discord's login form and relaunched successfully without another
  Electron download. Reports are in `final-live-download/`.
- Arch and Fedora containers installed their packages and checked file access,
  Electron loading and offline cache reuse as an ordinary user.
- The generated Arch recipe also passed its container build and payload check
  with Electron excluded from both package sources and installed files.
- Regression tests cover upgrading to package pins matching an already updated
  Electron, first download without an approved release, disabled automatic checks,
  failed downloads, damaged-cache repair, offline startup and setup cancellation.
- The native first-download dialog was inspected on Arch/niri Wayland. Closing
  the real GTK window cancelled and reaped a simulated download; closing its
  input pipe under Xvfb exited successfully. The temporary custom-drawn preview
  was removed. First launch also skips OpenAsar's automatic settings popup;
  manual access and saved settings remain available.
- `bun run test` passed 225 Python tests and 54 JavaScript tests; `bun run lint`
  passed. No account login, calls or physical camera tests were performed.

These checks do not establish a full Ubuntu AppArmor-restricted desktop session.

### Desktop identity, 27 October

`out/desktop-identity/` retains this worktree's test reports. On Arch Linux with
niri/Wayland, the original GTK download window reported `app_id=python3`.
After the fix, the GTK dialog, forced Zenity 4.2.2 fallback and bootstrap error
dialog all reported `discord-canary`. Closing the progress input completed both
native progress implementations successfully.

A private app built from the current client lock (Canary 1.0.2357) reused the
verified Electron 46.0.0-alpha.1 archive. Fresh-profile launch and relaunch reached
the login form; every recorded Wayland toplevel used `discord-canary`. The launch
checker now fails if any recorded application ID differs from the desktop entry.
No account login, call, camera capture or screenshot was performed.

An isolated Xvfb check confirmed the GTK dialog's two `WM_CLASS` components are
`discord-canary` and its `_NET_WM_ICON` contains a 128×128 icon. The installed and
generated desktop entries, plus autostart output from the pinned OpenAsar source,
passed `desktop-file-validate`. Packaging tests cover the desktop/icon installation
paths, loader identity and exact logo bytes. `bun run test` and `bun run lint`
passed. GNOME/KDE grouping and a full X11 desktop session remain untested; portable
Wayland launches still need a matching desktop entry and icon installed.

### Minimal bootstrap validation, 27 October

`out/minimal-bootstrap-check/` retains the local artifacts. Qualification reused
the approved Electron 46.0.0-alpha.1 archive and Canary 1.0.2380 pins. No Electron
compilation, account login, call or camera capture was performed.

- All four installer payloads match and exclude OpenAsar, ASARs, patch scripts,
  Python libraries and Electron. Installed bootstrap code totals about 17 KB;
  the portable archive is 0.88 MB including the icon. The separately signed
  application archive is about 5.5 MB.
- Fresh-profile startup and relaunch reached Discord's login form. The signed
  application-upgrade fixture then changed OpenAsar, the ASAR loader, patch code
  and Python updater, activated a new client compatibility generation, and
  launched successfully without downloading Electron again. Original application
  files remained unchanged. See `ready-upgrade/application-update-report.json`.
- The upgrade fixture uses a disposable signing key and local release responses;
  it does not publish or approve a real release. The same check now gates release CI.
- Arch and Fedora containers installed the minimal packages as root, then verified
  readable files, desktop integration, runtime libraries and offline cache reuse
  as an ordinary user. The generated Arch development recipe also passed with its
  application archive excluded from the installed package.
- Regression checks cover forged/mismatched approvals, changed archive bytes,
  incompatible bootstrap APIs, damaged caches, startup rollback, simultaneous
  launches, package upgrades and application-only restart notifications.
- `bun run test` passed 253 Python and 58 JavaScript tests; `bun run lint` passed.
  These checks do not establish live-call behavior or a complete Ubuntu desktop
  under AppArmor. The release workflow retains its Debian GUI check.

The subsequent bootstrap reliability pass adds recovery for cancelled initial
launches, malformed state or generation metadata, missing launcher permissions,
and exited but unreaped trial processes. Downloads retry transient failures up to
three times and discard partial files. Eleven regression tests cover these cases,
actual concurrent first resolves and disk-full extraction failure without changing
the working generation. The installed code remains stdlib-only, now about 21 KB.

`bun run test` passed 264 Python and 58 JavaScript tests; `bun run lint` passed.
`out/bootstrap-reliability/` retains all four matching installer payloads and a
fresh-profile signed upgrade that reached Discord's login form before and after
activation, kept the original code unchanged and reused Electron without another
download. No account login, call, physical camera test or new compilation was performed.

### Temporary-profile quota failure, 27 October

A fresh `/tmp/discord-test.G3QKG4` profile failed with `EDQUOT` while Krisp's
atomic patch step copied its binary. `/tmp` is a 16 GiB tmpfs mounted with
`usrquota`; `quotactl_fd(Q_GETQUOTA)` reported a 13,041,660 KiB user limit and
13,154,164,736 bytes used after failure cleanup (12.44 GiB limit, 12.25 GiB used).
The disk-backed home filesystem still had about 31 GiB free. This is quota
exhaustion on a separate filesystem, not a full SSD. The
[kernel tmpfs documentation](https://docs.kernel.org/filesystems/tmpfs.html)
describes these independent user quotas.

The startup UI now distinguishes quota exhaustion from `ENOSPC`, with regression
coverage for Python's errno text and `EDQUOT`. The portable test app was refreshed.
The documented fresh-profile command puts the profile and `TMPDIR` under
`~/.cache/dpe-tests`. A fresh native Xvfb/X11 launch there reached the login form
and verified client pins, reusing the retained runtime. No account interaction
was performed. An initial fixture attempt used a long repository path for
`TMPDIR` and hit Chromium's Unix socket path limit; the short home-cache path
avoids that as well. Reports and logs are in `out/tmp-quota/`, with the successful
fixture location recorded in `smoke-dir.txt`. All 318 Python tests, 75 JavaScript
tests and `bun run lint` passed.
