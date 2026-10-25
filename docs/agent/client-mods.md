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

## Validation

`tests/test_client_mods.py` checks archive parsing, custom paths, invalid loaders
and read failures. `tests/test_installation.py` covers both mods across module,
runtime, bootstrap and host updates, broken launcher links, initial stock
migration, stale/missing backups, switching, uninstall and activation rollback.

The release action now runs `scripts/check_client_mods.py` with the existing
checksum-pinned Vencord 1.4.2 and Equilotl 2.3.0 installers. It uses a fresh profile
and a relocated app copy with a changed bootstrap, then checks installed,
updated, relaunched and uninstalled states at the real Discord login form.
There is no reinstall between installation and update verification. The runtime
is reused; no account login, call, message or camera capture is performed.

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
