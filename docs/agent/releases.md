# Release packaging

Run **Release Linux packages** from GitHub Actions. Supply the full `ci-state`
commit for the client snapshot and choose a channel (or all three). The default
runtime is the reviewed `last_good_runtime` in `config/update-policy.json`.
Leave **publish** off to get downloadable Actions artifacts; turn it on to create
a GitHub prerelease after every required job passes.

This workflow never compiles Electron. It verifies the compiler artifact,
assembles the locked clients with the existing patches, moves the app directory,
and checks plain/Equicord launch and relaunch. Parallel jobs exercise native
video/Krisp and isolated X11/Wayland audio. nFPM then makes `.deb` and `.rpm`
packages from the same files used in the portable `.tar.gz`. Payload hashes are
compared across all three formats. A Debian install and launch run on Ubuntu;
Fedora checks RPM installation, file access and runtime loading as an ordinary user.
Fedora's full desktop UI is not exercised by that container check.

Versions use the UTC packaging time (`YYYYMMDD.HHMMSS`) so a Discord-only update
advances the package version even when Electron stays unchanged. Each manifest
records both upstream versions, source commit, runtime key and asset hashes.
Publishing is manual for now; it does not bypass qualification or enable AUR
publishing. The discovery workflow continues to qualify client updates separately.

## Build locally

After the [contributor setup](../../CONTRIBUTING.md), install `brotli`, a C compiler,
Go, `bsdtar` and `rpm`. Install the pinned packaging tool:

```sh
go install github.com/goreleaser/nfpm/v2/cmd/nfpm@v2.48.0
```

Put Go's binary directory on `PATH`, then use fresh output directories:

```sh
GH_REPO=discord-patched-electron/discord-patched-electron \
  uv run python scripts/fetch_reference.py 40053107555 --output out/release-runtime
uv run python scripts/build_local_app.py --portable --discord canary \
  --artifact out/release-runtime --clients config/clients.lock.json \
  --cache out/downloads/canary --output out/release-app
mv out/release-app 'out/relocated app'
uv run python scripts/check_desktop_launch.py 'out/relocated app' \
  --output out/release-launch --relaunch
uv run python scripts/package_release.py 'out/relocated app' \
  --output out/packages --version 20261024.120000 --epoch 1792843200
uv run python scripts/check_release_packages.py out/packages
```

Use a current immutable client snapshot instead of the bootstrap lock for a real
release. The timestamp above is an example; use a new UTC time for each release.
Local packaging requires a verified portable candidate and a passing launch check.
The GitHub publish job adds native/audio qualification and Equicord checks.

## Runtime details

Portable bootstraps resolve resources and modules from `DPE_APP_ROOT`, set by the
launcher from its own resolved location. They don't rewrite files at launch.
The same tree works under `/opt/<package>` or in a user-owned directory, including
when Equicord loads a copy of the bootstrap. Arch's existing fixed-root recipe
remains supported. Desktop entries are for installed packages; tarball users run
`launch.sh` directly.

The namespace sandbox stays enabled. Portable launchers disable only the setuid
helper. Debian packages include a scoped AppArmor user-namespace rule for Ubuntu.
No package enables audio services or overwrites user config. No runtime source
flags are changed during packaging.

Formatting native build scripts changes their content fingerprint. The previous
verified runtime remains accepted through its exact reviewed capacity digest;
this cleanup does not require a C++ rebuild or move retained Ninja objects.
Future compiler jobs use the new source fingerprint normally.
