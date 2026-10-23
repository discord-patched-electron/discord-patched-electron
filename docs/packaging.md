# Binary package candidates

`scripts/generate_package.py` generates one AUR recipe from a locked Discord
client, immutable runtime/camera-helper asset descriptors and a package revision. The shared
template derives from the imported `discord_arch_electron` package. The nine
Discord/Electron combinations have distinct executable names, desktop files,
installation directories and default profiles. They do not provide or conflict
with system Electron or the official Discord package.

A runtime descriptor contains `version`, `runtime_key`, `url` and `sha256`.
The URL must name a fixed ZIP asset in this repository's GitHub Releases.
All three Discord packages using the same runtime reuse that URL and checksum.

```sh
python3 scripts/generate_package.py out/runtime.json \
  --camera-support out/camera-support/camera-support.json \
  --discord ptb --electron stable --pkgrel 1 --output out/aur/ptb-stable
cd out/aur/ptb-stable
makepkg --printsrcinfo > .SRCINFO
makepkg
```

Generation does not publish, validate the runtime or allocate `pkgrel`. Those
decisions still require the release ledger and compatibility results. Do not push
a candidate to AUR simply because generation or makepkg succeeds.

The recipe downloads official client/module distributions with pinned SHA-256
checksums. It copies only the client's resources and modules, applies the existing
ASAR/bootstrap changes, runs the validated upstream Krisp patcher and preserves
Krisp's assets and `KMS/logs`. `!strip` keeps its native symbols. The runtime ZIP
includes Electron's notices and sandbox executable. Live text license downloads
retain the original AUR recipe's `SKIP`; binary inputs never use it. Preparation
does not launch Discord or access a user's profile.

Packaging uses Arch's `asar`, `python-capstone` and `python-pyelftools` packages,
as the upstream recipe does. Clean-container reports record their installed
versions. Project unit tests use `requirements.txt`; the byte-level Krisp
validator also checks the exact allowed edit in the Arch environment. The Arch
base image is pinned, while its package repositories remain rolling. This is
input traceability, not a claim of byte-for-byte package reproducibility.

## Packaging checks

`Checks` includes an Arch container job that runs `scripts/smoke_packages.py` for
all nine pairs. It downloads the real pinned clients/modules but supplies an
explicit, nonfunctional runtime fixture. The test runs the exact generated
PKGBUILD with normal dependency checking, generates `.SRCINFO`, validates the
desktop files and inspects the package archives. It checks private module roots,
disabled desktop updater, preserved Krisp bytes/assets, sandbox mode and notices.
Fixture packages are discarded; CI retains diagnostic logs, installed tool
versions and reports, each explicitly marked not publishable.

This check does **not** establish Electron linking/dependency completeness,
launchability, native streaming, effective Krisp suppression or per-app audio.
Those checks must run again with the compiled custom runtime before release.
The initial local host check is recorded in
[`evidence/packaging-local.json`](evidence/packaging-local.json); clean Arch CI
provides the stronger ongoing packaging check.

## Independent camera support binary

`python scripts/build_camera_support.py --output out/camera-support` runs in the
standard GitHub Actions checks, separately from the Electron compiler. Its small
x86-64-v3 library and compiler/source manifest are retained as `camera-support`.
The publisher must upload `libdpe-camera-probe.so` at the manifest's immutable
`camera-<binary-sha256>` release before using that manifest for an AUR update.
This publication step remains part of the unfinished release coordinator.

`makepkg` downloads and verifies the precompiled helper; it does not invoke a C
compiler. Preparation identifies the exact capability-probing function in each
locked voice ELF, retains its hash/range, and fails on unknown layouts. The launcher
scopes interception to that path and range. Package inspection checks the helper,
unchanged voice binary and scope metadata. An integration/helper update changes
package identity while preserving Electron compilation inputs.
