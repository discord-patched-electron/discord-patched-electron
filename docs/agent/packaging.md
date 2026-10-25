# Historical binary package candidates

The published AUR package now uses the complete CI-built tarball; see
[release packaging](releases.md). This document describes the older source-staging
recipe retained for regression tests. Stable and PTB are disabled.

`scripts/generate_package.py` generates one AUR recipe from client identity metadata,
immutable runtime/camera-helper asset descriptors and a package revision. The shared
template derives from the imported `discord_arch_electron` package. The three
Discord clients on prerelease Electron have distinct executable names, desktop files,
installation directories and default profiles. They do not provide or conflict
with system Electron or the official Discord package.

A runtime descriptor contains `version`, `runtime_key`, `url` and `sha256`.
The URL must name a fixed ZIP asset in this repository's GitHub Releases.
All three Discord packages using the same runtime reuse that URL and checksum.

```sh
uv run python scripts/generate_package.py out/runtime.json \
  --camera-support out/camera-support/camera-support.json \
  --discord canary --electron prerelease --pkgrel 1 --output out/aur/canary-prerelease
cd out/aur/canary-prerelease
makepkg --printsrcinfo > .SRCINFO
makepkg
```

Generation does not publish, validate the runtime or allocate `pkgrel`. Those
decisions still require the release ledger and compatibility results. Do not push
a candidate to AUR simply because generation or makepkg succeeds.

The recipe downloads the verified runtime, camera helper and checksum-pinned
OpenAsar source. It stages the project-owned loader, integration code and notices;
no Discord archive or native module is included. `asar` builds the small loader.
The Python patching dependencies and Brotli are runtime dependencies because
modules are downloaded and prepared in the user's profile on launch. See
[the module updater](client-updater.md) for activation and rollback behavior.

## Packaging checks

`Checks` includes an Arch container job that runs `scripts/smoke_packages.py` for
enabled Discord clients on prerelease Electron. It downloads OpenAsar source and
supplies an explicit, nonfunctional runtime fixture. The test runs the exact
generated PKGBUILD with normal dependency checking, generates `.SRCINFO`, validates
the desktop files and inspects the package archives. It rejects bundled Discord
client data and checks the project loader, sandbox mode and notices.
Fixture packages are discarded; CI retains diagnostic logs, installed tool
versions and reports, each explicitly marked not publishable.

This check does **not** establish Electron linking/dependency completeness,
launchability, native streaming, effective Krisp suppression or per-app audio.
Those checks must run again with the compiled custom runtime before release.
The initial local host check is recorded in
[`evidence/packaging-local.json`](../evidence/packaging-local.json); clean Arch CI
provides the stronger ongoing packaging check.

## Independent camera support binary

`uv run python scripts/build_camera_support.py --output out/camera-support` runs in the
standard GitHub Actions checks, separately from the Electron compiler. Its small
x86-64-v3 library and compiler/source manifest are retained as `camera-support`.
The publisher must upload `libdpe-camera-probe.so` at the manifest's immutable
`camera-<binary-sha256>` release before using that manifest for an AUR update.
This publication step remains part of the unfinished release coordinator.

`makepkg` downloads and verifies the precompiled helper; it does not invoke a C
compiler. Module preparation at launch identifies the capability-probing function
in the downloaded voice ELF, retains its hash/range and fails on unknown layouts.
The launcher scopes interception to that path and range. Package inspection checks
the helper; isolated native tests validate voice compatibility separately. An
integration/helper update changes package identity while preserving Electron
compilation inputs.
