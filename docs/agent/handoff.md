# Local Canary test build

Updated 24 October 2026. **Discord Canary 1.0.2365 with the compiled Electron
46.0.0-alpha.1 runtime opens successfully on native Wayland.** It requires an
x86-64-v3 CPU (AVX2; no AVX-512 requirement).

## Release policy and hosted compilation, 24 October

New compilation uses the budgeted Blacksmith workflow. The old PC-only instruction
and one-shot capacity workflows are superseded. No compiler was dispatched for
this change. See [runner policy](ci-setup.md#blacksmith-compilation) for timeout,
allowance reservations and the remaining cold-build/cache limitations.

AUR publication now requires both stable GitHub status and an approval record for
the exact manifest. Manual retries cannot bypass this. The existing AUR test build
stays frozen at `20261024.162607-1` until explicit approval. Do not approve a release
on the user's behalf. Older AUR notes below describe the previous policy.

Release notes use an AUR/Repo column and put test results beside the full changelog.
The latest test release remains public and unapproved. Two superseded app releases
are removed, with their Git tags retained for changelog comparisons. Runtime cache
releases stay in the separate cache repository.

Validation passed 119 Python tests, 16 JavaScript tests, formatting, linting and
actionlint. A live attempt to publish the latest test release was rejected before
download or SSH, including the verified-directory path. The latest release's
asset IDs and digests are unchanged. No AUR write or new compilation was performed.

## Documentation and channel switches, 24 October

The README now uses the supplied PNG artwork, with its editable Krita source kept
beside it. It distinguishes alpha/beta Electron from public test releases, explains
Canary compatibility, and directs unofficial-package issues to this repository.
Release notes use a distribution/download table. Older release assets keep their
original filenames and checksums.

[`config/channels.json`](../../config/channels.json) is the single source for
enabled channels. Only Canary and alpha/beta Electron are enabled. The
[channel guide](channels.md) describes the disabled options. Release, validation,
source-check and package entry points now read the switches instead of hard-coding
Canary. **Qualify compiled runtime** dispatches qualification for enabled clients.
Client-only updates still share one approved runtime.

Local validation passed 111 Python tests, 16 JavaScript tests, formatting, linting
and actionlint. A live read-only release preparation verified the discovered client
snapshot and downloaded only the pinned compiler report. Native build inputs are
unchanged from the preceding commit. No Electron compilation or physical camera
test was started. The disabled tracks have not received new end-to-end qualification.

The first CI pass caught a missing `channels.py` in the standalone Arch recipe.
The generator and template now include it. A regression test runs the real
preparation copy step outside the repository and imports its Python entry point.
The final artwork files use the user's newer PNG export and matching
`discord-patched-electron.png.kra`, stored as `discord-patched-electron.kra` here.
The corrected commit `8df1bc2` passed [all GitHub checks](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/40114953501),
including standalone Arch packaging, source assembly, native modules and audio.
The live README image, About description and all three release download tables
were verified. Release assets and approval status are unchanged.

## Cache, release and AUR publishing, 24 October

The [new public candidate](https://github.com/discord-patched-electron/discord-patched-electron/releases/tag/canary-1.0.2365-8748f4696bd6b2f6)
is `20261024.162607` (Canary 1.0.2365 / Electron 46.0.0-alpha.1). All four package
filenames are now fixed. It remains a **prerelease**, pending the user's stable
approval. The user explicitly requested initial AUR publication; the
[AUR package](https://aur.archlinux.org/packages/discord-canary-prerelease-patched-electron-bin)
is published as `20261024.162607-1` under `he3als`, with commit
`3dc49a45f0bbd7e3841f9081283997924b154fd0`. Its three files were read back and
matched against the verified recipe. Future approvals publish AUR updates;
**Publish AUR package** supports explicit publication or retries without approving
GitHub releases. Do not approve as stable on the user's behalf.

Compiler artifacts moved to `discord-patched-electron/cache`, with identical
checksums; the old cache release and tag were removed from the app repository.
`CACHE_TOKEN` is configured for future cache uploads. `AUR_SSH_PRIVATE_KEY` uses
the existing approved AUR key, with strict host-key verification. No secrets are
in source. No compiler was started.

Package updates refresh an existing mod restore backup only when the app matches
its new checksum. Vencord and Equicord both pass official install, upgrade,
reinstall, launch/relaunch and removal checks. Users can update normally and
rerun the official installer without uninstalling first.

[Release qualification](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/40111540714)
and [repository checks](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/40112372445)
passed. The release includes Arch/Debian/RPM/tarball checks and AUR build/install
validation. Local Arch reinstall testing also verifies the stale mod backup is
refreshed. Native tests use synthetic audio and isolated profiles; live calls and
microphone continuity remain unverified. No physical camera capture occurred.

Two test issues surfaced: diagnostic PCM buffered across phase boundaries, and
the hidden-device positive control queried an asynchronously refreshed list once.
The fixture now explicitly stops and flushes recordings per phase and awaits that
control within a bounded deadline, retaining device diagnostics. Production audio
adapters and native modules did not change. See
[audio evidence](../evidence/audio-recorder-flush.json) and
[publishing evidence](../evidence/release-cleanup.json).

The downloaded pacman package is at
`out/approve-release/canary-1.0.2365-8748f4696bd6b2f6/discord-canary-prerelease-patched-electron-bin-x86_64.pkg.tar.zst`.
The README is shorter, explains prerelease/stable status, and carries the requested
experimental warning. User docs explain the separate settings/login/cache directory.

## Wayland audio test correction, 24 October

`6ff29e5` removes the fixture's `offloadAdmControls=false` override. The shipped
voice wrapper already defaults to dedicated audio controls. With the forced
synchronous path, discovery run `40100767363` stalled inside native
`StartRecording`; the device-enumeration timeout was a later symptom. One local
repeat also stalled during a source switch. Six corrected runs passed across
manual/automatic selection and both audio servers, with the same runtime, voice
module and adapter hashes as the failing run. No runtime patch or longer timeout
was needed. CI verifies both bundled and compiled runtimes. See
[results](../evidence/wayland-audio-defaults.json).

[Checks](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/40106601920)
and [release qualification](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/40106617738)
passed. The new [public test release](https://github.com/discord-patched-electron/discord-patched-electron/releases/tag/canary-1.0.2365-e638bfba564a2ac4)
has all four package formats and remains unapproved. It reuses compiler run
`40053107555`; no Electron compilation or physical camera test occurred.

Project choices use neutral wording. First person is reserved for personal use,
testing and approval. The AI disclosure stays in the README.

## Public Canary packages and approval, 24 October

Only **Canary + prerelease Electron** is active. Stable/PTB remain in historical
locks but are rejected by build/validation entry points. User docs explain that
prerelease Electron brings Chromium's ongoing Wayland/Linux improvements sooner,
and Canary keeps compatibility testing focused on Discord's earliest updates.

Implementation `ef8cdac` published the first complete public test release:
[Canary 1.0.2365 / Electron 46.0.0-alpha.1](https://github.com/discord-patched-electron/discord-patched-electron/releases/tag/canary-1.0.2365-2e32a06a2c47d0f9).
It contains `.pkg.tar.zst`, `.deb`, `.rpm`, `.tar.gz`, a manifest and checksums.
All four formats contain the same 1,509 app files. Arch/Fedora installation and
unprivileged runtime loading passed; Ubuntu installed launch/relaunch passed.
Native video/Krisp, X11/Wayland audio, and plain/official Equicord launches passed.
No Electron compilation, account login, call or camera capture occurred.

The package is also downloaded and hash-verified locally:
`out/approve-release/discord-canary-prerelease-patched-electron-bin-20261024.125646-1-x86_64.pkg.tar.zst`.
The release remains **public and awaiting maintainer testing**. The read-only
`scripts/releases.py verify <tag>` command passed the same provenance and asset
checks used by approval. **Do not approve on the user's behalf.** They should test
the installed package, then run **Approve tested release** with its exact tag and
the testing checkbox. Approval promotes those files and pins their runtime;
it never rebuilds or replaces packages. GitHub **Watch → Custom → Releases**
supplies notifications. No new API keys are required.

Discovery now calls reusable release qualification, and successful candidate
compiler runs also start it. An identical public candidate skips packaging.
`43de871` fixes reconciliation of GitHub's shortened skipped reusable-job names.
AUR publishing remains disabled pending its credentials; approval currently
promotes GitHub releases only. See [release packaging](releases.md) and the
[validation evidence](../evidence/canary-public-releases.json).

## User guides and shared mod instructions, 24 October

The README now leads with features and patches. User guides omit runner details,
queue timing and old implementation history. `docs/client-mods.md` covers both
Vencord and Equicord; choose Custom Location with the official installer.
Vencord 1.4.2 passed installation, native Wayland launch/relaunch and restoration
of the original ASAR on uninstall. This used a fresh profile and no account.
The current Vencord installer lacks Equilotl's stale-backup handling, so the
shared guide tells users to uninstall the mod before updating Discord.

`uv sync --locked` now includes actionlint. `bun run lint` runs it alongside the
existing Ruff, Oxlint/Oxfmt, shell and native checks. Go setup was removed from
Checks and release CI; nFPM uses its checksum-pinned official binary. See
[validation evidence](../evidence/user-docs-and-tooling.json). Implementation
`0a7602e` passes [Checks](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/40094176275)
and the [Canary release dry run](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/40094176848).
The prebuilt nFPM produces matching tarball/Debian/RPM payloads; Ubuntu launches,
Fedora installation, native video/Krisp and audio checks pass. No public release
was published and no Electron compile was started.

## Bun and official mod installation, 24 October

Current tooling uses Bun 1.4.4 with `bun.lock`, plus uv. Node 24 remains required
by ASAR tools and `node:test`. README and CONTRIBUTING describe CI ownership,
contributor setup, release gates and the remaining test limits.

New apps use Electron's packaged entry point with `runtime/resources` symlinked
to `../resources`. The official Equilotl installer works with `-location <root>`;
no `equicord.py` or custom mod loader is shipped. The old command-line entry point
caused Equicord's `require.main` lookup to fail on Electron 46. CI now installs,
launches, uninstalls and checks the restored bootstrap before making packages.
Old helper instructions below apply only to the older local installation.

The implementation at `f6204f1` passes [Checks](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/40092738013)
and the [all-channel release dry run](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/40092737843).
All three formats pass payload checks, Ubuntu installed launch/relaunch and Fedora
installation/unprivileged runtime loading. Native video/Krisp and both audio
server fixtures pass. Local checks: 96 Python tests and 16 JavaScript tests.
See [Bun and installer evidence](../evidence/bun-official-installer.json).

The new portable, unmodded Canary app is at:

```text
/mnt/storage/discord-patched-electron-ci/local-tests/bun-official-installer-canary-20261024/launch.sh
```

It uses the package profile (without the older local build's `-local` suffix).
The existing desktop shortcut still points to the older installation. Official
installer install/relaunch/removal and reapply-after-update were tested on
fresh profiles and disposable trees. No account login, messages, calls, camera
media or desktop screenshots were used. No Electron compile was started.

## Repository cleanup and portable packages, 24 October

The user-facing docs are now short guides under `docs/`; implementation history
is indexed here in `docs/agent/README.md`. Use `uv sync --locked`, `bun install --frozen-lockfile --ignore-scripts`, `bun run test` and `bun run lint`. Native patches and upstream
snapshots were not reformatted. The old compiler stays reusable through the
reviewed capacity digest; no Electron rebuild was started for this cleanup.

`release.yml` builds relocatable tarballs and Debian/RPM packages from a verified
runtime. See [release packaging](releases.md). A moved Canary build passes native
Wayland launch/relaunch both plain and with Equicord. Local package payloads match;
Ubuntu 24.04 and Fedora 44 install checks pass. See
[cleanup evidence](../evidence/repository-cleanup.json) for paths and limits.
[Main checks](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/40089265553)
and the [all-channel release dry run](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/40089383775)
pass on `90f3cc9`: all three formats, Ubuntu installed launch/relaunch, Fedora
installation and unprivileged runtime loading, native video/Krisp and all audio
fixtures. Publication was disabled; packages are retained as Actions artifacts.
The earlier app shortcut below still points to the existing local installation.

## Run it

```sh
/home/he3als/scratch-ai-repo/discord-patched-electron/out/canary-prerelease-test/launch.sh
```

The shortcut points to:

```text
/mnt/storage/discord-patched-electron-ci/local-tests/canary-equicord-latest-20261024/launch.sh
```

Keep that installation directory in place: its launcher contains an absolute
path. It uses a separate profile at
`~/.config/discord-canary-prerelease-patched-electron-bin-local`, leaving the
stock Discord profile separate. Close the old instance before launching the new
one. Flags can be placed one per line in
`~/.config/discord-canary-patched-electron.conf`; the older package-specific
`-flags.conf` is still read. See [launcher, codecs and shortcuts](../linux-launcher.md).

Wayland selection is automatic. Application audio and the camera startup helper
are enabled by default. `DPE_APP_AUDIO=0` and `DPE_CAMERA_FAST_PROBE=0` disable the
respective adapters. niri supports automatic shared-window audio matching; other
compositors use the audio-app picker. See [Wayland audio](../wayland-audio.md).

## Client updates and Equicord, 24 October

The current build uses freshly discovered Canary 1.0.2365 and the existing
compiled runtime. Its bundled upstream Electron bytes were unchanged from
Canary 1.0.2357; no C++ build was needed. Equicord is installed in a separate
user-owned directory and passes native Wayland first launch/relaunch on a fresh
profile, including renderer, preload and DiscordNative checks. It can be disabled
with `DPE_EQUICORD=0`. See [Equicord](../client-mods.md) and
[update ownership/scheduling](discovery.md).

The update coordinator now checks Discord every 15 minutes independently of the
96-hour routine Electron cadence. It tracks shipped runtime bytes, not Discord
fork commits. Public releases/AUR remain a separate release qualification step.

## Follow-up fixes, 24 October

This local app reuses the verified compiler artifact; no C++ rebuild or
Blacksmith job was started. It includes:

- Electron 46 safeStorage compatibility, verified through real synchronous IPC
  and decryption after relaunch. Actual Canary first launch and relaunch both
  reach the login form on a fresh fixture profile with no uncaught exception.
- Internal audio sinks hidden from Discord's enumeration/hotplug API, verified
  with both PipeWire-Pulse and real PulseAudio plus PipeWire.
- Asynchronous native Linux camera initialization: engine queue stall reduced
  from 1,130 ms to 25.3 ms on the physical camera. This removes the measured
  stall; a sustained microphone/live-call transmission test remains unperformed.
- Automatic application audio on niri, including browsers. The individual-stream
  picker was removed at the user's request: audio from the app's other windows
  is included again. Other desktops retain the manual application picker when
  no reliable automatic match is available.
- Hyprland/Sway window labels and optional KDE/GNOME metadata, with dependency
  notes in the picker. Only the verified niri capture relationship enables
  automatic matching; these additional metadata providers do not.
- Matching desktop/portal identities and a private native shortcut test covering
  press and release. The desktop portal's own support/permission is still required.
- Overridable launcher defaults for native Wayland and hardware decoding.
  Hardware WebGL and H.264 VA-API decoding pass. Forced Chromium hardware encoding
  crashes the tested AMD path, so it remains disabled by default; software
  H.264/VP8/VP9/AV1 encoding passes. Discord's separate native codec negotiation
  was not exercised in a live call.

All three recipes pass an Arch packaging test with locked real client modules
and a dummy runtime. Runtime evidence uses the actual compiled Electron.
See [follow-up evidence](../evidence/desktop-fixes-local.json).
The current app additionally restores automatic browser audio; see
[restoration checks](../evidence/automatic-audio-restored.json).

The local desktop entry is installed at
`~/.local/share/applications/io.github.discord_patched_electron.DiscordCanary.desktop`.
Remove it when switching to the future AUR installation so it does not shadow
the system desktop entry. The old absolute installation remains unchanged;
use the shortcut above to get these fixes.

## Verified results

[Compiler run 40053107555](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/40053107555)
completed on the PC, using 20 compiler tasks at nice level 10 and one concurrent
linker. No Blacksmith compiler was used. The final resumed portion took 1,776
seconds; this is not the duration of a clean build.

The [retained evidence](../evidence/prerelease-runtime-local.json) records:

- Actual Canary bootstrap reaches the login form on native Wayland with a fresh
  profile. No account was used and no message or call was sent.
- Frame capture starts and stops correctly, survives forced garbage collection,
  and stops exactly once when either target or consumer is destroyed.
- Native video delivery/teardown and offline Krisp suppression pass locally for
  Canary and in CI for Stable, PTB and Canary against this exact runtime.
- Hardware WebGL uses the AMD RX 7900 GRE; H.264 playback uses VA-API without extra
  GPU flags.
- The physical UVC webcam reaches its first frame in **2.33 seconds** with the
  helper, versus **8.37 seconds** upstream in the same compiled runtime.
  Camera tests retain timing, dimensions and version metadata only. No webcam
  images/video were displayed, saved or uploaded; no microphone was recorded.

The final custom-runtime [Linux audio job](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/40055239278)
passed all ten fixtures: three X11 and seven Wayland scenarios, with both
PipeWire-Pulse and real PulseAudio plus PipeWire, including manual selection.
The [native/Krisp/lifetime job](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/40055239309)
also passed. Unchanged-input build reuse was verified without starting GN or a
compiler.

## Preserved build state

The successful runtime archive is retained in the compiler artifact and local
build cache. Its SHA-256 is
`f70c12b4c543773964107c4c77c1ee121d81b3d723e60ea84fac114110ee498a`.
Completed-artifact reuse checks this hash and all native inputs before skipping
compilation.

The current workspace identity is
`83555d7644725992ae9ce46af4ec0367d955aacab7d0fe9ff65a67ba0cab7862`, a symlink to
physical directory
`f6a6a79b20ad975b5e64ad64d45df92dd007d514785f46cd66cd58baed91a390` under
`/mnt/storage/discord-patched-electron-ci/build-cache/prerelease/`.
Keep the physical checkout path stable. Moving it changes generated Clang module
inputs and forces unnecessary recompilation. Never force reuse by changing Ninja
timestamps or dependency records.

Actual Chromium module/plugin flags bypass sccache. The verified reuse mechanisms
are completed runtime artifacts and retained Ninja objects; see
[cache limitations](build-caching.md). The later restarts demonstrated object
reuse while keeping the physical path stable. Live runner controls are described
in [CI setup](ci-setup.md#changing-local-limits-while-the-build-runs).

The older compiled Electron 44 reference test app remains available at
`out/canary-reference-test/launch.sh` for comparison.

## Remaining release work

This is a local test build, not a public release. Real Discord calls, GNOME/KDE
sessions and sandboxed applications remain separate qualification work. No claim
is made that every Linux audio or camera issue is fixed.

The three prerelease-Electron recipes exist and changed clients now trigger
qualification on the retained tested runtime. Eligible Electron prereleases
preflight the patch queue before scheduling the PC compiler. Automatic candidate
promotion/public release/AUR publication remain unfinished, and no AUR publishing
key is configured. The tested runtime archive has a public prerelease asset for CI reuse; no
client package release or AUR update has been published.
See [current update behavior](discovery.md).

The [latest source checks](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/40085203754)
and [three-channel update qualification](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/40084710165)
passed. A [repeat discovery run](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/40085341335)
scheduled no client tests or compiler. See
[update/Equicord evidence](../evidence/update-policy-equicord-local.json).

Local monitor state is in
`/mnt/storage/discord-patched-electron-ci/overnight-monitor.json`.
The original unattended build finished and the scheduled build monitor remains
disabled. The follow-up interactive fixes do not trigger hibernation.
