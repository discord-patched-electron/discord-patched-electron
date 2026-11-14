# Release packaging

Only Canary with alpha/beta Electron is enabled. Client/application changes reuse
an approved runtime; source changes need a budgeted [compiler run](ci-setup.md).
Review every [upstream diff](upstream-update-review.md) before qualification.

## Create a candidate

For a locally compiled runtime, pin the runtime key and cache asset checksums in
`config/local-runtime.json`, then run **Import local Electron runtime** on `main`.
The import checks the compiler report against current native inputs and retains
the local build provenance. Its successful run starts the same qualification
workflow below without allocating a compiler runner.

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
tags are immutable. See [signing trust](#signing-trust).

## Runtime reuse

Published runtimes target x86-64-v3/AVX2 without requiring AVX-512. The GN
custom toolchain wraps pinned Chromium Clang/sysroot with
`-march=x86-64-v3 -mtune=generic`. Baseline flags precede dispatched target flags;
Skia's explicit v4 variants remain intact. Host tools, Rust and proprietary native
modules retain their own settings. No `-march=native` or fast-math override is used.

`scripts/runtime_fingerprints.py` defines native identity; application adapters,
ASAR changes, settings, capture helpers and tests can reuse Electron. Prefer a
verified completed archive, then retained Ninja objects. Historical full-build
sccache statistics show 6,755 requests with zero hits/writes (5,501 module
bypasses, 1,254 unsupported flags); increasing its cache does not fix those calls.

Hosted compatible builds use stable `/tmp/dpe-electron-build` slots, a journaled
Git/gclient transition and bounded checkpoints. Major/toolchain/native-config
boundaries use separate slots. Checkpoint validation preserves nanosecond file
and directory mtimes and rejects escaping links, devices, traversal, wrong roots
and checksum/size mismatches. Retained physical paths matter to Clang modules;
do not move objects or falsify timestamps to force reuse. Existing local builds
keep per-input layouts unless incremental mode is explicitly selected.

The compressed checkpoint cap is 20 GB. Caches are disposable; eviction or a
failed replacement upload can require a cold build. Runtime artifacts remain
separate. Provenance exceptions require `validate_provenance`, the exact project
compiler run URL and matching retained build identity; foreign URLs or missing/changed
provenance cannot activate an exception. No paid sticky disk or Electron-contributor Reclient service is enabled.
Auto scheduling reserves 2 GiB, budgets 2 GiB/task, considers cgroup/affinity
limits and caps at 32 tasks, with one linker and at most eight sync jobs.
See [measured reuse](benchmarks.md#build-reuse).

## Source ports

`config/sources.lock.json` pins source anchors, ordered patch checksums,
replacement paths, upstream-equivalent explanations and assembled Git trees.
`patches/discord/44/integration.patch` is the imported delta; port layers live
under `patches/ports/<major>/`. Preserve the imported files unchanged.

An excluded import path needs a literal reviewed replacement or an explicit
upstream equivalent in the lock. No wildcard exclusions, automatic conflict-side
selection or silent patch skipping. Exact anchors must reproduce the locked tree;
three-way reuse on later versions must stop on incompatible changes. Source
assembly, dependency application, syntax checks, linking and runtime qualification
are separate gates. Disabled/source-only ports are not qualified runtimes.

Preserve upstream ownership/API changes while adding Discord bindings: cppgc
BrowserWindow/FrameCapture tracing, current view pointers, shared renderer bindings,
MaybeLocal failure propagation, native property templates and current popup/child
view ownership. Active FrameCapture roots survive until their asynchronous stopped
event after Mojo/source cleanup. Do not restore removed V8/Pinnable interfaces.

Dependency context refreshes retain OpenH264 failure cleanup/flush, packet-to-frame
metadata, all pending restriction promises and frame-scoped audio authorization.
`ports.46.dependency_notes` explains substantive substitutions. The committed
Linux-x64 FFmpeg roll is generated with pinned Clang and Chrome branding; it
preserves GIF/AAC/MP4 and dynamic OpenH264 support without replacing other target
lists. Ordinary builds apply the roll rather than regenerating it.

[Source preflight](../evidence/remaining-port-preflight.json) establishes syntax,
not linking. Later AV1 crop, presentation clock and content-only media ports need
matching compiled-runtime evidence; earlier alpha.1 results cannot qualify them.

## Package boundaries and recovery

The tarball, Arch, Debian and RPM installers carry the same minimal stdlib
bootstrap, pins, public key, desktop entry, icon and notices. `application.zip`
contains integration code and a complete file index. Neither installer nor app
archive contains Electron or proprietary Discord modules. Validate hashes across
formats, native dependencies, licenses and resource links after relocation.
AppImage qualification runs without FUSE and must avoid bundling host GPU drivers
or glibc; use its dependency inventory and launch checks rather than copying the
builder's whole library tree.

To qualify unpublished installer payloads, supply `--application-archive` and
`--runtime-archive` to the desktop checker. It seeds fresh caches through the real
verification path. Ubuntu AppArmor rules must cover both private final
`app-*/electron` and `dpe-runtime/**/electron` paths; allowing only the splash
binary is insufficient. Keep Chromium sandboxing enabled. Containers prove
installation/access/loading, not a complete desktop or call.

Candidate proposals record exact client/runtime/source identities in
`config/update-proposals/`; they do not approve anything. Reserve the source tag
before a proposal metadata write and verify it still pins qualified source.
Retries resume drafts and reuse identical public candidates; never overwrite
public assets. Build-version ordering determines release order. Notes compare
against the prior approved release for the same channel/track, including merged
code and helper changes, rather than the preceding test release.

`uv run python scripts/releases.py refresh-notes` previews note repairs under
`out/release-notes/`; `--apply` is a publication action requiring authorization.
AUR metadata-only changes increment `pkgrel`; identical retries do nothing.
An archive change under an existing version is rejected. The standard Canary
identity conflicts with stock Canary; current enabled tracks come from
`config/channels.json`, not historical multi-channel results.

## Signing trust

The packaged key authenticates checksum lists, which authenticate release
metadata and all archive hashes. Keep private backups outside Git. Signing-key
rotation changes package trust and needs explicit package/key migration; extending
expiry of the same key differs from replacing it. Verify the current fingerprint
and expiry from `packaging/release-signing-key.asc` before maintenance. Publication,
manual approval and AUR writes remain separate authorized operations.
