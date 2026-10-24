# Local Canary test build

Updated 24 October 2026. **Discord Canary 1.0.2365 with the compiled Electron
46.0.0-alpha.1 runtime opens successfully on native Wayland.** It requires an
x86-64-v3 CPU (AVX2; no AVX-512 requirement).

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
with `DPE_EQUICORD=0`. See [Equicord](../equicord.md) and
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
