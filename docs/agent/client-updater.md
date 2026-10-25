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

- The package owns patched Electron, bootstrap source and Linux integrations.
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

The retained local packages are unsigned development outputs, not published
releases. The compiler runtime was reused unchanged. Desktop compatibility
transforms are checked using its Node mode before activating each new client.

Run `bun run test` and `bun run lint`. Updater regression tests cover host-download
exclusion, source validation, module reuse, failed-update rollback, offline startup,
archive boundaries and package payload rejection. Brotli is needed for archive
tests. Real desktop and package checks remain necessary after bootstrap changes.

Automatic updates can introduce an incompatible upstream desktop module between
runtime releases. The compatibility patches deliberately reject unknown layouts.
Old generations and cached modules currently require manual cleanup after closing
Discord; automatic garbage collection is not implemented.
