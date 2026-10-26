# Client and Electron updater

Packages contain patched Electron, an OpenAsar-based bootstrap and Linux
integration code. Discord's proprietary modules download directly from Discord
into the user's profile. The app never downloads Discord's stock Electron.

This page describes the current design. See [installation](../install.md) for
user instructions and [updater history](updater-history.md) for earlier designs
and detailed test records.

## What can update

The package pins exact module URLs, revisions and SHA-256 hashes in
`client-update.json`. It also ships the initial runtime identity, bootstrap and
release public key. Installing a test package explicitly selects its candidate.

Automatic client and Electron updates require a signed project release and a
matching manual approval record in `main/config/approved-release.json`. The tag,
package version, manifest hash and named approver must agree. A signature or
GitHub's **Latest** label alone is insufficient. Module changes under the same
Discord version need approval too.

The updater checks on launch and every 15 minutes. It prepares downloads in the
background and offers Discord's normal restart notification. Activation happens
at startup. Failed checks retain the package baseline or a previously approved
installation. Discord's hosted web interface updates independently.

Package updates supply bootstrap, launcher, integration and signing-key changes.
The automatic updater handles Discord modules and patched Electron.

## Client downloads and approval

`<profile>/dpe-updater/` contains verified downloads, prepared generations and the
`current.json` pointer. A lock serializes updates. The updater:

1. Selects the package pins or the exact client pins from an approved release.
2. Verifies each archive before extraction. Traversal, external links, device
   files, duplicate entries and oversized archives are rejected.
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
before selection. Failed checks keep the current runtime; a damaged cache falls
back to the packaged runtime.

Runtime generations live under `dpe-runtime/<installation-id>/generations/`.
Installation IDs separate portable copies and system apps. Package files remain
unchanged, and a newer system package supersedes an older cached runtime.
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

The bundled runtime opens the 300×300 startup screen before Python preparation.
`startup.cjs` manages the window and retries; `runner.cjs` manages the cancellable
helper group and bounded JSON-lines output. Preparation begins at DOM readiness
without waiting for optional media downloads. Diagnostics go to stderr.

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
pyelftools ship from checksum-pinned wheels in `uv.lock`, with their sources,
native library and notices. Helpers use the package's `python/` directory and
disable user site packages and bytecode writes. Preparation prerequisites are
checked when needed, so missing patch tools do not block a usable cached client.

## Validation

Run `bun run test` and `bun run lint`. Regression coverage includes approval,
archive boundaries, module reuse, damaged-cache repair, activation rollback,
offline startup, mod preservation and helper cancellation. GPG fixtures verify
real signatures. Brotli is required for archive tests.

Release launch checks set `DPE_CLIENT_PINNED=1` and `DPE_RUNTIME_BUNDLED=1`.
Reports compare prepared module URLs, revisions and hashes with the packaged
pins. Packaging rejects untested or changed pins, proprietary client payloads,
media assets and generated bytecode. The same pins enter signed `release.json`.

The [25 October test records](updater-history.md) include fresh-profile Wayland
login/relaunch, official mod installers, downloaded-runtime launch, installation
recovery, startup errors and all four package formats. Tests reused the verified
Electron 46.0.0-alpha.1 runtime. Ubuntu/Fedora container checks do not establish a
full AppArmor-restricted desktop session. Live calls and physical power-loss
recovery remain untested; interruption recovery uses failure injection.
