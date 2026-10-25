# Module-only Linux client

The Arch [Discord recipe](https://gitlab.archlinux.org/archlinux/packaging/packages/discord/-/blob/main/PKGBUILD)
now installs a launcher and `updater_bootstrap`. That installer downloads a host
archive containing both Discord's bootstrap ASAR and stock Electron, plus separate
module distributions. Inspection of the October 25, 2026 manifest and native
updater found no resource-only host download option.

This branch therefore uses pinned OpenAsar startup code with a project-owned
module updater. It consumes the new `updates.discord.com/distributions/app/manifests/latest`
API, validates module URLs and SHA-256 hashes, and never fetches `full.url` or host
deltas. OpenAsar's upstream native updater, legacy downloads, self-update,
performance flags, remote splash/settings UI and renderer customization are
replaced or disabled. All executed bootstrap source and its AGPL license ship
in the package. `resources/app.asar` contains only the project's entry point and
metadata, preserving the layout used by official mod installers.

## Ownership and updates

- The package provides the initial patched Electron, bootstrap source and Linux integrations.
- `$DISCORD_USER_DATA_DIR/dpe-updater` owns downloaded modules, complete generations
  and the atomic `current.json` pointer. No install hook downloads or launches Discord.
- A lock serializes updates. Archives are checksum-verified before extraction;
  extraction rejects traversal, external links and device files. Failed downloads
  or patch validation leave the current generation unchanged.
- Module content hashes allow reuse across host versions. Compatibility code
  changes create new prepared modules. Old generations are retained so running
  clients are never rewritten underneath Electron.
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
