# Vencord and Equicord update preservation

The module-only updater publishes the running app under
`<profile>/app-<version>`. Official installers patch that directory's
`resources/app.asar` and keep the project's bootstrap in `_app.asar`.
The package's own resources remain separate from this writable installation.

`integration/updater/client_mods.py` recognizes the two-file ASAR written by the
official [Vencord installer](https://github.com/Vencord/Installer/blob/main/app_asar.go)
and [Equilotl](https://github.com/Equicord/Equilotl/blob/main/app_asar.go). It reads
the archive header, package entry point and single `require()` call without
executing JavaScript. Vencord targets `patcher.js`; Equicord targets an ASAR,
including custom `EQUICORD_DIRECTORY` paths. It retains the exact loader bytes
and target path. A leftover `_app.asar` or mod settings directory does not prove
that a mod is installed. Read errors abort publication rather than discard a mod.

`installation.py` uses the active version in `installer.db` to find the loader,
even if the profile launcher link is missing or broken. On first takeover it
uses the profile's launcher or newest app directory (numeric version ordering),
then falls back to a directly patched package. Once a profile installation is
active, it remains authoritative: uninstalling or switching mods must not
resurrect a loader from an old version, package copy or recovery backup.

Before activation, the updater wraps the newly prepared bootstrap with the
detected loader and writes the new bootstrap to `_app.asar`. Module, host,
Electron and package updates share this path. The existing activation journal
and SQLite transaction retain the previous mod and bootstrap if activation
fails. Missing or stale profile backups are repaired from the package baseline.
Old directly patched packages are unwrapped in the private prepared view so
their loader can migrate into the profile without undoing a later uninstall.

## Mod self-updates

The preserved loader continues to require the user's original payload path.
Neither payloads nor mod settings enter the updater's prepared views, package
snapshots or rollback transaction:

- [Vencord's HTTP updater](https://github.com/Vencord/Vencord/blob/main/src/main/updater/http.ts)
  downloads its `patcher.js`, preload, renderer, CSS and related assets, then
  writes them in the existing `dist` directory.
- [Equicord's HTTP updater](https://github.com/Equicord/Equicord/blob/main/src/main/updater/http.ts)
  downloads `desktop.asar` and writes it over the installed ASAR using
  `original-fs`. The loader retains the installed filename, including a custom
  `EQUICORD_DIRECTORY` location.
- The mods' Git updaters build in their existing checkout. Keeping the original
  target also preserves rebuilt files and their checkout-relative paths.

`DISABLE_UPDATER_AUTO_PATCHING=1` only disables the mods' fallback host repatching;
it does not disable either mod's payload updater. The project handles host
publication and preserves the loader separately. If a mod updates while a
client update is being prepared, even a failed client activation must leave
the newer mod payload in place.

## Validation

`tests/test_client_mods.py` checks archive parsing, custom paths, invalid loaders
and read failures. `tests/test_installation.py` covers both mods across module,
runtime, bootstrap and host updates, broken launcher links, initial stock
migration, stale/missing backups, switching, uninstall and activation rollback.
Additional cases update the external mod payload after preparing a client view,
then exercise runtime, module, bootstrap, host-version and relocated-package
updates. They cover in-place writes, atomic replacement by a rebuild, preserved
settings, and a self-update during failed client activation and retry.

The release action now runs `scripts/check_client_mods.py` with the existing
checksum-pinned Vencord 1.4.2 and Equilotl 2.3.0 installers. It uses a fresh profile
and a relocated app copy with a changed bootstrap, then checks installed,
updated, relaunched and uninstalled states at the real Discord login form.
There is no reinstall between installation and update verification. The runtime
is reused; no account login, call, message or camera capture is performed.

`tests/client-mods/self-update.cjs` runs each downloaded official bundle's actual
HTTP updater handlers with controlled release responses. It first checks that a
download failure leaves existing files intact, then serves a changed payload
containing a renderer marker and allows the updater's real filesystem writes.
The desktop check verifies that this marker executes before and after the client
update and relaunch, and compares every updated payload's hash. This tests the
real updater implementation without depending on a newer upstream release being
available. Git network/build commands are not invoked; their replacement-file
behavior is covered by regression fixtures.

Local validation on October 25, 2026:

- `out/preserve-client-mods-live/mod-report.json`: both pinned installers passed
  all five launch states on Arch Linux / niri / native Wayland with Canary
  1.0.2367 and retained Electron 46.0.0-alpha.1. Each loader remained byte-for-byte
  identical across the changed bootstrap; uninstall restored the new bootstrap.
- `out/preserve-client-mods-tests.log`: full Python and JavaScript suites passed.
- `out/preserve-client-mods-lint.log`: required lint and formatting checks passed.

Host-version changes, failed activation and damaged launcher links were exercised
with isolated regression fixtures; the live qualification used a same-version
package update. No package was published or system installation changed.

Self-update follow-up validation:

- `out/preserve-mod-self-updates-live/mod-report.json`: the actual Vencord and
  Equicord HTTP handlers applied changed local release payloads. All twelve
  fresh-profile login checks passed; the new renderer marker executed after
  each mod self-update, client package update and relaunch. Updated payload
  hashes remained identical and normal uninstall still restored the bootstrap.
- `out/preserve-mod-self-updates-tests.log`: 211 Python and 51 JavaScript tests
  passed, including self-update during failed client activation and retry.
- `out/preserve-mod-self-updates-lint.log`: all required checks passed.

Release artifacts retain the mod report and updater-handler reports alongside
the launch results; generated mod payloads and profiles are not uploaded.
