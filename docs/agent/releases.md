# Release packaging

Only Discord Canary and prerelease Electron are active. Historical locks and
upstream templates can still describe other channels; entry points reject them.

## Automatic candidates

**Discover upstream releases** polls Discord and reserves work by content identity.
A changed Canary host or module starts **Release Linux packages** immediately on
the approved runtime. Routine Electron updates have a 96-hour cooldown; identified
high/critical fixes can bypass it. Discord's bundled executable hash, rather than
fork commits, detects changes to its own Electron. A successful **Candidate
Electron build** also starts package qualification with that runtime.

Each candidate uses an immutable client snapshot. The workflow verifies the
compiler artifact, assembles the client, moves the app directory, and checks plain
launch and relaunch. It checks the official Vencord and Equicord installers across installation, an
app upgrade, reinstallation, launch/relaunch and removal. Both must restore the
current bootstrap before packaging.
Parallel jobs check bundled/native video and Krisp, plus isolated X11/Wayland audio.

nFPM makes `.pkg.tar.zst`, `.deb` and `.rpm` packages from the same app tree as the
portable `.tar.gz`. Payload hashes are compared across all four formats. Ubuntu
checks Debian installation and GUI launch; Arch and Fedora containers install the
packages and check file access and runtime loading as an ordinary user. Those
containers don't verify a full desktop session or live call.

After all checks pass, CI uploads every asset before publishing a public GitHub
prerelease. Release notes include upstream versions, installation instructions,
a link to check results and commits since the preceding app release. Runtime
cache tags are excluded from the changelog.

Candidate tags include the client version and a content hash. A repeat of the same
runtime/client/integration inputs skips packaging if that release is already
public. Failed work remains visible and can be retried with GitHub's **Re-run all
jobs**. A partial draft can be resumed; public assets are never overwritten.

## Test and approve

1. Select **Watch → Custom → Releases** on the repository and enable your preferred
   notification delivery in GitHub settings.
2. Open the new public prerelease. Download its `.pkg.tar.zst` and `SHA256SUMS` into
   a new folder. Check the hashes and install it:

   ```sh
   sha256sum --check --ignore-missing SHA256SUMS
   sudo pacman -U ./discord-canary-prerelease-patched-electron-bin-*.pkg.tar.zst
   ```

3. Launch **Discord Canary (Patched Electron)**. Test the features you use,
   including voice, camera startup, screen sharing with app audio, and Krisp.
   Reinstall client mods after updating; uninstalling first is unnecessary.
4. Run [**Approve tested release**](https://github.com/discord-patched-electron/discord-patched-electron/actions/workflows/approve-release.yml)
   on `main`, paste the exact release tag, and tick the local-testing confirmation.

Approval checks the successful main-branch workflow, all package hashes and the
retained runtime. It records the approved version, updates the runtime used for
future client-only updates, then marks that release **Latest**. It doesn't rebuild,
re-upload or rename any package. It rejects an older candidate after a newer
release has been approved. The four-day cadence is based on compilation time,
not how long I took to approve it.

Approval also publishes the AUR recipe using `AUR_SSH_PRIVATE_KEY`. It installs the
same verified portable app under `/opt/`; no patching or downloads happen during
installation. `makepkg` verifies the pinned archive checksum, and `.SRCINFO` is
produced with `makepkg --printsrcinfo`. Publishing is serialized and rejects older
versions. **Publish AUR package** can retry a failed push or explicitly publish a
public test release without marking it stable.

## Manual candidates and local packaging

Run [**Release Linux packages**](https://github.com/discord-patched-electron/discord-patched-electron/actions/workflows/release.yml)
with **publish** enabled to create a public candidate. Leave the snapshot blank to
use the latest discovered Canary, or provide a full `ci-state` commit. Leave the
compiler run blank to reuse the approved runtime. Turn **publish** off for Actions
artifacts only. No packaging job compiles Electron.

After the [contributor setup](../../CONTRIBUTING.md), install `brotli`, a C compiler,
`bsdtar`, `rpm` and the [prebuilt nFPM 2.48.0 binary](https://github.com/goreleaser/nfpm/releases/tag/v2.48.0).
Put `nfpm` on `PATH`. No Go installation is needed. CI verifies the official archive's
pinned checksum. Use fresh output directories:

```sh
GH_REPO=discord-patched-electron/discord-patched-electron \
  uv run python scripts/fetch_reference.py 40053107555 --output out/release-runtime
uv run python scripts/build_local_app.py --portable --discord canary \
  --artifact out/release-runtime --clients out/clients.lock.json \
  --cache out/downloads/canary --output out/release-app
mv out/release-app 'out/relocated app'
uv run python scripts/check_desktop_launch.py 'out/relocated app' \
  --output out/release-launch --relaunch
uv run python scripts/package_release.py 'out/relocated app' \
  --output out/packages --version 20261024.120000 --epoch 1792843200
uv run python scripts/check_release_packages.py out/packages
```

Fetch `out/clients.lock.json` with `scripts/fetch_client_snapshot.py` using a healthy
immutable state commit first. Replace the example runtime and timestamp as needed.
Local packaging needs a verified portable candidate and passing launch check;
public release qualification adds the remaining CI checks.

## Runtime details

All released packages install the portable tree under `/opt/<package>`. A tarball
can run anywhere. Bootstraps find resources through `DPE_APP_ROOT`, set by the
launcher. `runtime/resources` points to `../resources`, matching the official mod
installers' expected app layout. The older generated AUR recipe uses `/usr/lib/`;
it is retained for validation, not published as the pacman test artifact.

The namespace sandbox stays enabled. Debian packages include a scoped AppArmor
user-namespace rule for Ubuntu. No package enables audio services or overwrites
user config. No runtime source flags change during packaging.

A checksum-pinned release in [the cache repository](https://github.com/discord-patched-electron/cache)
retains compiler output beyond Actions artifact expiry. `CACHE_TOKEN` needs Contents
read/write access to that repository only; the regular workflow token handles app releases. That release is a CI cache, not app approval. The last approved
capacity digest remains usable when unrelated packaging code changes, so client
updates don't need an Electron rebuild or lose retained Ninja objects.

## Package updates

Release filenames are fixed; versions remain in package metadata, release tags
and manifests. AUR download aliases include the version to avoid stale local
source caches. Older public assets remain immutable.

Package post-install/upgrade hooks refresh `_app.asar` only when it exists and
`app.asar` matches the newly installed checksum. This keeps official mod installers
from restoring an old bootstrap, without touching a currently installed mod loader.
Tarball users extract into a fresh directory and rerun their official installer.
