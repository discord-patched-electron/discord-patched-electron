# Release packaging

Use this guide to package a verified runtime, test a candidate and approve it
for users. Only Canary with alpha/beta Electron is enabled; see
[channel switches](channels.md) for other tracks. Installing the app is covered
in the [user guide](../install.md).

## Automatic candidates

**Discover upstream releases** polls Discord and reserves work by content identity.
A changed Canary host or module starts **Release Linux packages** immediately on
the approved runtime. Routine Electron updates have a 96-hour cooldown. Identified
high/critical fixes can bypass it. Discord's bundled executable hash, rather than
fork commits, detects changes to its own Electron. A successful **Candidate
Electron build** starts **Qualify compiled runtime**, which runs package
qualification for each enabled Discord channel with that runtime.

Each candidate records an immutable client snapshot for native regression tests
and release identity. The package itself contains no Discord client. First launch
downloads the exact package-pinned modules; launch reports verify those pins and
record the tested generation. See [the module updater](client-updater.md).
Qualification checks the verified runtime, bootstrap and relocated app through
launch and relaunch. Both official mod installers are tested across installation,
updates and removal; uninstall must restore the current bootstrap. Parallel jobs
check native video, offline Krisp and isolated X11/Wayland audio.

nFPM makes `.pkg.tar.zst`, `.deb` and `.rpm` packages from the same minimal
bootstrap tree as the portable `.tar.gz`. All four contain only the standard-library
loader, application download pins, trusted key, icon, desktop entry and notices.
OpenAsar, patch code, updater libraries and Linux adapters are in the separately
signed `application.zip`; neither archive bundles Electron. The application
contains its exact archive hash and runtime identity in `runtime-update.json`. The verified
full app used for qualification is retained unchanged. Bootstrap hashes are compared across all four formats, and the application index
and client pins are verified separately. Ubuntu
checks Debian installation and GUI launch. Arch and Fedora containers install the
packages and check file access and runtime loading as an ordinary user. Those
containers don't verify a full desktop session or live call.

After all checks pass, CI uploads every asset before publishing a public GitHub
test release. Release notes include upstream versions, a distribution/package table,
a link to check results and at most five relevant project commits since the
preceding release for the same channel and Electron track. All supported tag
formats are recognized; ordering uses build versions rather than publication
time. Notes show Discord/Electron version changes and same-version module or
runtime changes. Approval commits, proposal records and documentation-only
commits stay out of the short list. The full comparison remains linked.

Before packaging a public candidate, CI commits its exact client pins, runtime
identity, source commit and discovery snapshot to
`config/update-proposals/<channel>-<track>.json` on `main`. The commit is named
**Propose Discord … / Electron …** and linked from the release notes; its SHA
is included in the signed manifest. This records a proposal, not approval.
Qualification continues on the original workflow source commit; the metadata
write does not alter tested code. Retries reuse the proposal commit, and an older
retry cannot replace a newer record. Tag reservation precedes the metadata commit
so the workflow token can reserve tags for workflow-changing commits.

Candidate tags identify the channel, release time and content hash. A repeat of the same
runtime/client/integration inputs skips packaging if that release is already
public. Failed work remains visible and can be retried with GitHub's **Re-run all
jobs**. A partial draft can be resumed. Public assets are never overwritten.

To repair historical descriptions, run `uv run python scripts/releases.py
refresh-notes` to preview all published app notes under `out/release-notes/`.
An optional release tag limits the selection. After reviewing the files, add
`--apply` with the normal project/main release environment. Only release bodies
change; package assets, tags, titles, approval status and Latest remain intact.

## Test and approve

1. Follow the one-time [signing-key setup](../install.md#arch-install-a-release-package-directly). Download
   the test release's `.pkg.tar.zst`, matching `.sig` and `SHA256SUMS` into a new
   folder. Check the hashes and install it:

   ```sh
   sha256sum --check --ignore-missing SHA256SUMS
   sudo pacman -U ./discord-canary-prerelease-patched-electron-bin-*.pkg.tar.zst
   ```

2. Launch **Discord Canary**. Test the features you use,
   including voice, camera startup, screen sharing with app audio, and Krisp.
   Check that any installed Vencord or Equicord still loads after updating.
3. Run [**Approve tested release**](https://github.com/discord-patched-electron/discord-patched-electron/actions/workflows/approve-release.yml)
   on `main`, paste the exact release tag, and tick the local-testing confirmation.

Unapproved release notes include an **Approve this release** button and the exact
tag to paste into the workflow. The link is public; GitHub requires repository
write access to run it. Approval removes the button from that release's notes.

To hear about new candidates, select **Watch → Custom → Releases** on the
repository and choose your delivery method in GitHub's notification settings.

Approval checks the successful main-branch workflow, all package hashes and the
retained runtime. It records the approved version, updates the runtime used for
future client-only updates, then marks that release **Latest**. It doesn't rebuild,
re-upload or rename any package. It rejects an older candidate after a newer
release has been approved. The four-day cadence is based on compilation time,
not how long I took to approve it.

Approval also publishes the AUR recipe using `AUR_SSH_PRIVATE_KEY`. It installs the
same verified portable app under `/opt/`. No patching or downloads happen during
installation. `makepkg` verifies the pinned archive checksum, and `.SRCINFO` is
produced with `makepkg --printsrcinfo`. Publishing is serialized and rejects older
versions. **Publish AUR package** only retries an approved stable release. Both
paths require a public, non-prerelease GitHub release and an approval record for
its exact manifest checksum. Changing the GitHub release label alone is insufficient.
Local recipe generation remains available for candidate testing.

The package name's `prerelease` component identifies alpha/beta Electron.
It does not bypass app-release approval. Earlier AUR policy changes are recorded
in [the release history](handoff-history.md).

Release asset names stay fixed. The AUR recipe uses a versioned local download
alias (`$pkgname-$pkgver.tar.gz::...`) so `makepkg` cannot reuse another version's
cached archive.

## Manual candidates and local packaging

Run [**Release Linux packages**](https://github.com/discord-patched-electron/discord-patched-electron/actions/workflows/release.yml)
with these inputs:

| Input           | Use                                                                                |
| --------------- | ---------------------------------------------------------------------------------- |
| `publish`       | Enable for a public candidate; disable for Actions artifacts only.                 |
| `clients_state` | Leave blank for the latest discovered Canary, or provide a full `ci-state` commit. |
| `reference_run` | Leave blank to reuse the approved runtime, or provide a successful compiler run.   |

No packaging job compiles Electron.

### Package locally

After the [contributor setup](../../CONTRIBUTING.md), install `brotli`, a C compiler,
`bsdtar`, `rpm` and the [prebuilt nFPM 2.48.0 binary](https://github.com/goreleaser/nfpm/releases/tag/v2.48.0).
Put `nfpm` on `PATH`; no Go installation is needed. CI verifies its archive
checksum. First choose a healthy, full 40-character `ci-state` commit, then fetch
that snapshot and a verified runtime. Use fresh output directories:

```sh
clients_state_commit=REPLACE_WITH_FULL_COMMIT
GH_REPO=discord-patched-electron/discord-patched-electron \
  uv run python scripts/fetch_client_snapshot.py "$clients_state_commit" --output out/clients.lock.json
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

Set `clients_state_commit` to your chosen commit and replace the example runtime
run and timestamps before running these commands.
Local packaging needs a verified portable candidate and passing launch check;
public release qualification adds the remaining CI checks.

## Runtime details

All released packages install the minimal bootstrap under `/opt/<package>`. A tarball
can run anywhere. Bootstraps find resources through `DPE_APP_ROOT`, set by the
launcher. Downloaded runtime generations link their resources to their immutable application
payload; the writable profile installation supplies the official mod
installers' expected app layout. The older generated AUR recipe also uses the
client-free layout under `/usr/lib/`; it is retained for validation, not published
as the pacman test artifact. It produces `application.zip` separately and installs
only its small bootstrap. Neither installer includes Electron.

Installed-package qualification seeds a fresh user's application cache from
`application.zip` and its runtime cache from the already verified `runtime-dist.zip`, using the updater's checksum, extraction and
version probe. This lets unpublished candidates run without waiting for a public
cache tag. For example, pass `--application-archive out/packages/application.zip` and
`--runtime-archive out/release-runtime/binary/runtime-dist.zip` to `check_desktop_launch.py` when testing an extracted installer payload. Container
checks also verify offline reuse of that cache. Public release publication retains
the runtime cache before exposing the installer assets.

The namespace sandbox stays enabled. Debian packages include a scoped AppArmor
user-namespace rule for Ubuntu; `aa-exec` also applies it to verified per-user
runtime updates. No package enables audio services or overwrites
user config. No runtime source flags change during packaging.

Release CI also grants user namespaces to executables under its private
`out/**/profile/app-*/electron` test paths. The launcher copies the verified runtime there before opening Discord;
qualification also grants namespaces to the private `profile/dpe-runtime/**/electron`
cache paths used before that copy. Allowing only the bundled
executable covers the splash but not the final client. Launch console logs are
retained with the reports to diagnose sandbox and startup failures.

A checksum-pinned release in [the cache repository](https://github.com/discord-patched-electron/cache)
retains compiler output beyond Actions artifact expiry. `CACHE_TOKEN` needs Contents
read/write access to that repository only. The regular workflow token handles app releases. That release is a CI cache, not app approval. The last approved
capacity digest remains usable when unrelated packaging code changes, so client
updates don't need an Electron rebuild or lose retained Ninja objects.

## Package updates

Automatic updates require a signed release and a matching manual approval record.
The signed manifest includes exact Discord module URLs, revisions and checksums
under `client`, alongside Electron's runtime-cache reference. Module-only updates
reuse the compiler output but still wait for manual approval.

Packages pin `application.zip` and its file index in `bootstrap.json`. The signed
release manifest includes that application's archive hash, index hash, version
and bootstrap API. Application payloads contain the baseline module and runtime
pins. Tests seed unpublished archives through explicit `--application-archive`
and `--runtime-archive` options; both still require the pinned hashes.
The approval action binds the application, client and runtime to the same
`manifest_sha256`. Once the new bootstrap is installed, OpenAsar, ASAR patches,
launcher behavior and the Python updater can all update without a package upgrade.
Only the stable loader, trust key, desktop integration and system dependencies
remain package-managed. See [application activation](client-updater.md#application-downloads-and-activation).

Release filenames are fixed. Versions remain in package metadata, release tags
and manifests. AUR download aliases include the version to avoid stale local
source caches. Older public assets remain immutable.

Minimal packages have no ASAR patching or mod-backup hook. The downloaded
application refreshes the writable profile installation while preserving mods.
Legacy release recipes retain their original backup hook for compatibility.
Tarball users extract into a fresh directory; mods in the standard profile survive.

## Release signatures

The publishing job signs the Arch package and `SHA256SUMS` only after all release
gates pass. It receives `RELEASE_SIGNING_KEY`, a dedicated Ed25519 key with no
personal signing keys. The public key is pinned in `packaging/release-signing-key.asc`
and uploaded with each release. New manifests require signatures for approval.
Signatures are verified against the pinned key before upload and approval.

The local backup and revocation certificate are in
`~/.local/share/discord-patched-electron/signing`, outside the repository. Back up
that directory privately. The key expires in October 2028. Before expiry, renew
it and update both the pinned public key and the Actions secret. Never log or
commit the private key. Published package archives and manifests are immutable.
Detached signatures can be added to an older release without replacing its files.

AUR metadata-only changes increment `pkgrel` when the archive URL and checksum
are unchanged. Repeating the same publication is a no-op. Archive changes require
a new `pkgver`. Desktop identity is recorded in new manifests so recipes can also
handle older archives with the previous desktop filename.

## Release tag ordering

New manifests use `tag_schema: 3`: for example,
`v20261025010203.0.0-canary.0127ebd36508aff4`. The first numeric component is
fixed-width UTC `YYYYMMDDHHMMSS`, so both lexical and semantic-version sorting
preserve chronological order, including hours before 10:00. Schema 2 dropped
leading zeroes from the time and therefore sorted incorrectly as text.
Discovery searches all release pages for the existing fingerprint before creating
another candidate. Draft retries retain the date and time encoded in their tag.
Older signed manifests and their download URLs remain valid; published tags are
never moved or renamed.

The [25 October cleanup](proprietary-payload-cleanup.md) retained approved tag
`v20261025.223908.0-canary.041bb4c70d3f760e` under its older schema. Its name and
assets remain immutable. New releases use schema 3; runtime-cache tags are
independent of app versions.

## Arch package conflicts

The AUR name/description and `provides` searches on 24 October 2026 found
[discord-canary](https://aur.archlinux.org/packages/discord-canary) and
[discord-canary-electron-bin](https://aur.archlinux.org/packages/discord-canary-electron-bin).
Both are explicit conflicts in generated Arch recipes and future Arch release
packages. The generic `discord-canary` conflict also covers other providers.
At the time, the official Arch repositories carried stable Discord, not Canary.

[moonlight-canary-bin](https://aur.archlinux.org/packages/moonlight-canary-bin)
installs its own executable, desktop file and icons. It is not a conflicting
Canary package. Stable, PTB, standalone mod installers and separate clients are
not blocked merely because they are related to Discord.
