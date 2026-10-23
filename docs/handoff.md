# Handoff and build monitoring

Updated 24 October 2026. The unattended task is to finish a working local Canary
build, check its native Wayland launch, camera, screen sharing/audio and Krisp,
then hibernate only after the checks and launch instructions are saved.
Physical webcam frames must never be saved or uploaded. Camera tests keep timing,
dimensions and version metadata only; do not take desktop screenshots during them.

## Current compiler queue

- [Reference 40036898682](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/40036898682)
  is compiling incrementally after the earlier two-task run was cancelled. It
  retains its original CPU flags and all usable objects.
- [Prerelease 40040828617](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/40040828617)
  is queued behind it at `ab9f0c2`: Electron 46.0.0-alpha.1 with x86-64-v3,
  16 Ninja tasks, one linker and 32 GiB sccache. Source assembly, all dependency
  patches, gclient hooks and GN generation already pass on the PC. GN generated
  38,464 targets. No candidate C++ compilation has completed yet.

Both use runner `he3als-pc-container` in Docker container `discord-electron-runner`.
The container has all 20 logical CPUs available, a 26 GiB RAM limit and 36 GiB
RAM-plus-swap limit. Both dispatches select 16 tasks. No Blacksmith compiler is
running or queued. The last reported Blacksmith allowance use remains 168 minutes.

```sh
tail -f /mnt/storage/discord-patched-electron-ci/actions/discord-patched-electron/discord-patched-electron/out/capacity/build.log
gh run view 40036898682 --repo discord-patched-electron/discord-patched-electron
gh run view 40040828617 --repo discord-patched-electron/discord-patched-electron
docker stats discord-electron-runner
```

The log path is shared by successive Actions checkouts; check the run ID before
interpreting it. Ninja's resumed-task counter is not a percentage of elapsed time.
Leave the PC/container running. No terminal must remain open. Do not restart a
healthy compiler or delete its persistent build cache. See [live runner controls](ci-setup.md#changing-local-limits-while-the-build-runs).

## Checks already passed

The [overnight baseline evidence](evidence/linux-overnight-baselines.json) records:

- Real Canary bootstrap reaches the login form with a fresh profile on native
  Wayland. The package-managed updater fix prevents a mandatory host update from
  blocking launch. This still uses bundled Electron, not the pending custom build.
- AMD hardware WebGL and H.264 VA-API decoding work with default Wayland selection.
- The physical UVC webcam reaches its first frame in 2.33 seconds with the helper,
  versus 8.33 seconds upstream in the latest alternating pair. No frames retained.
- Isolated real native Wayland/audio tests pass on PipeWire-Pulse and real
  PulseAudio plus PipeWire, including process exit, source changes and cleanup.
- All three generated Arch packages pass preparation/packaging with real Discord
  payloads and an explicitly nonfunctional runtime fixture.

The dependency port passes 72 Python tests and source reconstruction; the audio
adapters pass 12 JavaScript tests. CI checks use standard GitHub runners. Native
video/offline Krisp and audio CI baselines use Discord's bundled runtimes until a
successful compiler artifact triggers the custom-runtime jobs.

## Completion sequence

1. Inspect the reference result and automatic native/audio validation. Diagnose
   concrete failures before resuming; a quiet log is not itself a failure.
2. Let the queued prerelease compiler reuse the prepared workspace. Its identity
   is `f775fe99a963381720338a1ad78e7d72ef5f3444acedc3725394752f61f0cc31` under
   `/mnt/storage/discord-patched-electron-ci/build-cache/prerelease/`.
3. Fetch the successful artifact using `scripts/fetch_reference.py`. Build the
   actual Canary folder with `scripts/build_local_app.py --artifact ...`, then
   run `scripts/check_desktop_launch.py`; see [exact commands](linux-runtime.md).
4. Check native video/Krisp, isolated Wayland audio, local graphics and physical
   camera metadata against the custom runtime. Use the camera probe's
   `--provenance` argument so its report identifies the completed compiler run.
   Never use an account, send messages or join a call unattended.
5. Save the runnable path and results before hibernating. Disable any scheduled
   monitor first. Hibernate only with a working build and no pending compiler.

Wayland audio is enabled by default; `DPE_APP_AUDIO=0` disables the adapter.
Exact automatic application matching is currently niri-specific. Other desktops
use an explicit audio picker. Real GNOME/KDE sessions, sandboxed applications and
microphone/Krisp calls remain separate release qualification work.

No public binary release or AUR update has been published. The three
prerelease-Electron recipes exist, but the compile/release/AUR coordinator is
unfinished and no AUR key is configured. The immediate overnight deliverable is
a local Canary test build; do not label it as a fully qualified public release.
