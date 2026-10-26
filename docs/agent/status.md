# Early implementation milestones

These results were recorded on 23–24 October 2026, before the current release and
approval flow. They preserve the evidence for the first working port. Use
[the handoff](handoff.md) for current work and [release history](handoff-history.md)
for later packaging milestones.

## Source and packaging

- The imported Discord patch reconstructed its fork tree exactly. Separate
  compatibility patches assembled the pinned Electron 45, 46 and 47 candidates.
  Skipped import paths had replacement patches or documented upstream equivalents.
- The AUR snapshot matched `3b290dffd5eb840faed445bdc2bdb18572839471`.
- Client locks separately pinned Stable 1.0.166, PTB 1.0.224 and Canary 1.0.2357.
  These were the enabled channels at the time; only Canary is enabled today.
- The existing Krisp patcher changed only the expected six-byte branch in each
  module and was idempotent. Static results are in
  [Stable](../evidence/stable-krisp.json), [PTB](../evidence/ptb-krisp.json) and
  [Canary](../evidence/canary-krisp.json) reports.
- Generated Arch recipes passed with real locked payloads and dummy runtimes.
  These checked package structure, metadata and native dependencies, including
  the narrow dispatch-library exception. See
  [packaging evidence](../evidence/packaging-native-dependencies-ci.json).

## Discovery and compilation

Discovery recorded moved tags and missed releases, kept durable content-based
state and avoided new work on unchanged polls. The first two CI polls passed;
the second left `ci-state` untouched. Discovered PTB/Canary snapshots then passed
separate native/Krisp baselines and package checks. See
[discovery evidence](../evidence/discovery-ci.json) and
[client-update evidence](../evidence/client-updates-ci.json).

Early compiler attempts failed during Sentry download and depot_tools bootstrap.
Recovery preserved the local checkout; [build capacity](build-capacity.md) records
the attempts. The reference run `40036898682` and Electron 46.0.0-alpha.1 run
`40053107555` subsequently completed.

The reference passed native Wayland Canary login, graphics/VA-API, native video,
offline Krisp and camera timing, plus three-channel native/audio CI.
The Electron 46 port passed those checks and capture-lifetime tests. See
[reference results](../evidence/reference-runtime-local.json) and
[prerelease results](../evidence/prerelease-runtime-local.json).

Candidate builds used x86-64-v3. Completed-artifact reuse and retained Ninja
objects worked. Small compiler-cache fixtures passed, but later full-build
statistics exposed sccache bypasses; see [build reuse](build-caching.md).

## Native video, audio and camera

All three bundled runtimes passed synthetic frame delivery/teardown and offline
Krisp off/on/off testing. The noise-only interval improved by 53.6 dB with speech
retained. [CI evidence](../evidence/native-ci.json) records exact inputs.
These were bundled baselines, not evidence of a custom runtime or live call.

Wayland became the primary audio target. The adapter used exact niri cast/window
matching, multiple audio processes and an explicit picker on other desktops.
[Checks 40030377851](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/40030377851)
passed three X11 and seven Wayland fixtures, covering both Pulse servers,
source changes, cancellation, Discord exclusion and helper-crash cleanup.
The mixed-server dropout was fixed by keeping Pulse streams on their server's
clock. See [audio reports](../evidence/wayland-audio-ci.json).

The completed Electron 46 runtime later passed the same ten audio fixtures.
The checks reused the runtime and cached compositor without another Electron build.

With the physical Logitech UVC camera and bundled Electron 44.9.3, the helper
reached the first 1280×720 frame in 1.74 seconds versus 6.05–7.78 seconds upstream.
The compiled Electron 46 comparison measured 2.33 versus 8.37 seconds.
Only timing/dimension metadata was retained. No frames were displayed, saved or
uploaded. See [camera startup](camera-startup.md) and
[local evidence](../evidence/camera-wayland-local.json).

## Limits of this milestone

Live Discord calls with microphone/Krisp and screen sharing, full GNOME/KDE
sessions and sandboxed applications were not established by these fixtures.
Publication, AUR credentials and the release coordinator were still pending at
this milestone; those historical blockers are superseded by [release packaging](releases.md).

## Probe incident

A bundled Discord executable ignored `ELECTRON_RUN_AS_NODE` and opened its normal
bootstrap/profile during a probe. It was stopped. Future probes must use isolated
profile paths and must not assume that the RunAsNode fuse is enabled.
