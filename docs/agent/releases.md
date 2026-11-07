# Release packaging

Only Canary with alpha/beta Electron is enabled. Client/application changes reuse
an approved runtime; source changes need a budgeted [compiler run](ci-setup.md).
Review every [upstream diff](upstream-update-review.md) before qualification.

## Create a candidate

Discovery normally starts qualification automatically. For a manual candidate,
run [Release Linux packages](https://github.com/discord-patched-electron/discord-patched-electron/actions/workflows/release.yml):

| Input           | Selection                                                        |
| --------------- | ---------------------------------------------------------------- |
| `clients_state` | Full immutable `ci-state` commit, or blank for latest discovery. |
| `reference_run` | Successful compiler run, or blank for the approved runtime.      |
| `publish`       | Off for artifacts only; on for a public test release.            |

Qualification checks packages, fresh-profile launch/relaunch, mods, native
video/Krisp and isolated audio. Public test releases remain unapproved until
manual testing. Failed runs keep the last qualified release available; retries
resume drafts without overwriting public assets.

## Test and approve

1. Verify the candidate’s signatures and checksums using the
   [installation guide](../install.md#arch-install-a-release-package-directly).
2. Install and test the exact candidate: startup, voice, camera, screensharing
   with app audio, Krisp and any installed Vencord/Equicord.
3. Run [Approve tested release](https://github.com/discord-patched-electron/discord-patched-electron/actions/workflows/approve-release.yml)
   on `main` with that exact tag and the local-testing confirmation.

Approval checks qualification and hashes, records the approved manifest, updates
Latest and publishes AUR metadata. It does not rebuild packages. Automatic updates
require both a signed release and its matching approval; a public test release or
GitHub’s Latest label alone is insufficient. Use **Publish AUR package** only to
retry publication of an already approved stable release.

## Package locally

After the [contributor setup](../../CONTRIBUTING.md), install `brotli`, a C compiler,
`bsdtar`, `rpm` and the [prebuilt nFPM 2.48.0 binary](https://github.com/goreleaser/nfpm/releases/tag/v2.48.0).
Put `nfpm` on `PATH`; no Go installation is needed. CI verifies its archive
checksum. First choose a healthy, full 40-character `ci-state` commit, then fetch
that snapshot and a verified runtime. Use fresh output directories:

```sh
clients_state_commit=REPLACE_WITH_FULL_COMMIT
runtime_run=REPLACE_WITH_SUCCESSFUL_COMPILER_RUN
package_version=REPLACE_WITH_PACKAGE_VERSION
source_epoch=REPLACE_WITH_UNIX_TIMESTAMP
GH_REPO=discord-patched-electron/discord-patched-electron \
  uv run python scripts/fetch_client_snapshot.py "$clients_state_commit" --output out/clients.lock.json
GH_REPO=discord-patched-electron/discord-patched-electron \
  uv run python scripts/fetch_reference.py "$runtime_run" --output out/release-runtime
uv run python scripts/build_local_app.py --portable --discord canary \
  --artifact out/release-runtime --clients out/clients.lock.json \
  --cache out/downloads/canary --output out/release-app
mv out/release-app 'out/relocated app'
uv run python scripts/check_desktop_launch.py 'out/relocated app' \
  --output out/release-launch --relaunch
uv run python scripts/appimage.py --install out/appimage-tools --dependencies
uv run python scripts/package_release.py 'out/relocated app' \
  --output out/packages --version "$package_version" --epoch "$source_epoch"
uv run python scripts/check_release_packages.py out/packages
```

Replace the four placeholders with your chosen snapshot, successful runtime run,
package version and source timestamp before running these commands.
Local packaging needs a verified portable candidate and passing launch check;
public release qualification adds the remaining CI checks.

## Release signatures

The pinned key is `packaging/release-signing-key.asc`. Signing uses the dedicated
`RELEASE_SIGNING_KEY` Actions secret after qualification. Keep private key backups
outside Git; renew the key before its October 2028 expiry. Published assets and
tags are immutable. See [signing and key maintenance](release-reference.md#release-signatures).

For package layout, cache identity, draft recovery, release-note refresh, tag
ordering and Arch conflicts, use the [release reference](release-reference.md).
