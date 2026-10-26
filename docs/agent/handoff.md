# Handoff

Start with [CONTRIBUTING.md](../../CONTRIBUTING.md) for setup and required checks.
This page points to current configuration and retained evidence. Earlier local
builds and release milestones are in [the history](handoff-history.md).

## Current configuration

- **Channels:** Canary and alpha/beta Electron, controlled by
  [`config/channels.json`](../../config/channels.json).
- **CPU:** x86-64-v3, including AVX2; AVX-512 is not required.
- **Client delivery:** packages contain patched Electron and an open-source
  bootstrap. The [updater](client-updater.md) downloads approved Discord modules.
- **Profiles and mods:** the standard `~/.config/discordcanary` profile is shared
  with Discord. Official Vencord and Equicord loaders survive updates.
- **Releases:** CI publishes test packages. Manual approval promotes the tested
  files and permits automatic updates and AUR publication. The approval record is
  [`config/approved-release.json`](../../config/approved-release.json).
- **Compilation:** new CI builds use the [budgeted Blacksmith workflow](ci-setup.md).
  Packaging reuses verified runtimes. Do not restart a healthy compiler or remove
  retained objects.

For package installation, use the [user guide](../install.md). For new packages,
use [release packaging](releases.md). Old local launch commands may use retired
profiles, desktop entries or mod helpers.

## Retained Electron build

Electron 46.0.0-alpha.1 from
[compiler run 40053107555](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/40053107555)
passed native Wayland launch and the checks below. The runtime archive SHA-256 is:

```text
f70c12b4c543773964107c4c77c1ee121d81b3d723e60ea84fac114110ee498a
```

Use the runtime recorded in [`config/update-policy.json`](../../config/update-policy.json)
for release work. Verify its report and archive before reuse.

The retained local workspace is under
`/mnt/storage/discord-patched-electron-ci/build-cache/prerelease/`:

- Identity: `83555d7644725992ae9ce46af4ec0367d955aacab7d0fe9ff65a67ba0cab7862`.
- Physical directory: `f6a6a79b20ad975b5e64ad64d45df92dd007d514785f46cd66cd58baed91a390`.

Keep the physical path stable. Moving it changes generated Clang module inputs
and causes recompilation even when objects remain. Never alter Ninja timestamps
or dependency records to force reuse. Completed archives and retained Ninja
objects are the verified reuse methods; [sccache has limitations](build-caching.md#current-full-build-cache-limitation).

The earlier Electron 44 comparison app was retained at
`out/canary-reference-test/launch.sh`. Check that any local artifact still exists
and matches its provenance before using it.

## Validation evidence

- [Compiled runtime](../evidence/prerelease-runtime-local.json): fresh-profile
  Canary login, native video and teardown, offline Krisp, capture lifetime, AMD
  WebGL, VA-API decoding and physical-camera timing. Earlier three-channel tests
  do not enable Stable or PTB today.
- [Desktop fixes](../evidence/desktop-fixes-local.json): safe storage, private
  audio-device filtering, asynchronous camera initialization and native shortcuts.
- [Application audio](../evidence/automatic-audio-restored.json): automatic niri
  matching and manual selection, including all streams from the selected app.
- [Mixer reset](../evidence/pulse-mixer-buffer-reset.json): stale samples cleared
  when switching audio sources.
- [Mod preservation](client-mods.md#validation): official installation, self-update,
  client update, relaunch and uninstall.
- [Updater checks](updater-history.md): module approval, installation recovery,
  package payloads and startup UI.
- [Cleanup audit](proprietary-payload-cleanup.md): removal of older packages that
  bundled proprietary client files.

Synthetic tests do not prove live-call quality. Remaining coverage includes
real calls with microphone/Krisp and screen sharing together, full GNOME/KDE
sessions, sandboxed applications, and an AppArmor-restricted desktop launch.
Camera measurements retain metadata only, never images or video.

The old overnight monitor remains disabled. Its state file is
`/mnt/storage/discord-patched-electron-ci/overnight-monitor.json`.
Do not restart it or hibernate the PC without a current instruction.
