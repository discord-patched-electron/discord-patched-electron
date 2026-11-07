# AppImage host library boundary — November 5, 2026

## Findings and changes

The old Ubuntu 22.04 dependency recipe explicitly bundled libpipewire, the
PipeWire client executables, SPA/PipeWire plugins and PipeWire configuration.
AppRun forced the image's plugin and configuration directories. It also exported
a separate GTK/NSS/PulseAudio/ALSA desktop library closure through
`LD_LIBRARY_PATH`, affecting Electron and every subprocess. An updated host could
therefore load its own libraries against older image plugins, or older image
libraries against host components. The reported exact 0.3.48 payload was not
available for inspection; the problematic recipe and overrides were confirmed.

PipeWire documents these variables as plugin/module search overrides:
[PipeWire environment](https://docs.pipewire.org/page_man_pipewire_1.html) and
[SPA plugin locations](https://docs.pipewire.org/page_spa_plugins.html).

The new boundary is:

| Component                                        | Packaging decision                                                                                                              |
| ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------- |
| Python                                           | Keep the pinned standalone runtime and standard library for bootstrap/update and audio orchestration.                           |
| GnuPG/gpgv, Brotli                               | Keep for signature verification and updater decompression without a host installation.                                          |
| Helper library dependencies                      | Keep linuxdeploy's helper-relative ELF paths; never export their directory through the loader environment.                      |
| PipeWire, SPA, PulseAudio, ALSA                  | Host libraries, clients, plugins and configuration. Both Python and JavaScript audio resolvers use host tools.                  |
| GTK, NSS, fonts, desktop integration             | Host libraries and tools, including Zenity, xprop and dbus-send. Zenity remains optional in the bootstrap.                      |
| Graphics/display stack, libc, C++ runtime        | Host-owned. No new copies or overrides.                                                                                         |
| Public CA bundle                                 | Keep as a fallback; prefer explicit or host trust stores.                                                                       |
| Electron, Discord, application payload, OpenH264 | Retain the existing verified download/update lifecycle.                                                                         |
| Licenses and package inventory                   | Retain build provenance and copyright/source notices. The inventory describes the build container, not only packaged libraries. |

AppRun preserves caller-provided `LD_LIBRARY_PATH`, `SPA_PLUGIN_DIR`,
`PIPEWIRE_MODULE_DIR`, `PIPEWIRE_CONFIG_DIR` and `XDG_DATA_DIRS` rather than setting
its own. `DPE_TOOLS_DIR` selects bundled Python; desktop/audio helpers stay in
`/usr/bin`. No system-wide environment or installed packages are changed.

Dependency metadata now uses schema 2. Generation, packaging and extracted-release
checks reject stale recipe/container/tool identities and known host-owned library
families, desktop closures, PipeWire directories and unexpected helper binaries.
This prevents an existing cached dependency tree from silently undoing the fix.
Use a fresh tools directory when regenerating dependencies.

## Validation

- `bun run lint` passed.
- `bun run test`: 706 Python tests passed; 426 JavaScript tests passed, 3 skipped.
- Shell syntax checks passed for AppRun and the staging recipe.
- Built dependencies with the pinned tools and Ubuntu container; the separate
  helper library closure contains assuan, bz2, gcrypt, readline, sqlite3 and tinfo.
  The standalone Python distribution retains its own private dependencies.
- `uv run python scripts/check_appimage.py --container` passed using the actual
  pinned builder/runtime, a bare Ubuntu image with no added packages, no network
  and UID 65534. Checks cover bundled Python/GnuPG/Brotli, CA trust, Brotli
  roundtrip, signature verification, absence of bundled desktop/audio tools,
  environment preservation, paths/arguments with spaces and exit status.
- The same real-image check passed on the host with custom library, SPA, PipeWire
  module/configuration and desktop data paths, proving AppRun preserves them.
- A staged AppRun probe with bundled Python loaded PipeWire 1.6.10, PulseAudio,
  GTK and NSS from the host's `/usr/lib`; `/proc/self/maps` confirmed none of
  those libraries came from the image. Result: `out/appimage-host-library-probe/result.log`.
- Regression tests reject old bundles and host stack additions, preserve native
  helper paths and bootstrap bytes, and verify launcher environment/argument/exit
  behavior for default and custom environments.

No new Electron compilation was needed. Real Discord calls, FUSE mounting and
cross-distribution GUI sessions were not exercised; bootstrap extraction tests
do not establish those. Users need a new AppImage for this packaging change and
an updated DPE application payload for the revised audio helper resolution.
