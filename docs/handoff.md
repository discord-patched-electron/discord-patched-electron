# Handoff and build monitoring

Updated 24 October 2026. The unattended task is to finish a working local Canary
build, check its native Wayland launch, camera, screen sharing/audio and Krisp,
then hibernate only after the checks and launch instructions are saved.
Physical webcam frames must never be saved or uploaded. Camera tests keep timing,
dimensions and version metadata only; do not take desktop screenshots during them.

## Current compiler queue

- [Reference 40036898682](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/40036898682)
  completed successfully. Its compiled runtime passes local Canary login on
  native Wayland, native video/offline Krisp, AMD WebGL/VA-API and the physical
  camera metadata test. All three channels also pass native/Krisp CI.
- [Prerelease 40050257141](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/40050257141)
  is compiling at `18c652f` with the frame-capture GC port: Electron 46.0.0-alpha.1 with x86-64-v3,
  16 Ninja tasks, one linker and a bounded 32 GiB sccache store. Live stats show
  Chromium module/plugin flags bypass sccache; persistent Ninja reuse remains
  effective. See [the full-build limitation](build-caching.md#current-full-build-cache-limitation). Source assembly, all dependency
  patches, gclient hooks and GN generation already pass on the PC. GN generated
  38,464 targets. The first candidate attempt stopped because a workflow
  `TARGET` environment variable conflicted with Chromium bindgen. Renaming it
  fixed both failed generators; this run resumes the same prepared workspace.
  A subsequent Skia failure exposed baseline flags overriding its dispatched v4
  target. The corrected generated toolchain now compiles that target successfully.
  The next failure was Discord FrameCapture's removed `Pinnable` API. Its new
  cppgc lifetime code and WebFrameMain caller now compile, with a separate
  checksum-pinned follow-up patch. Moving the physical checkout changed generated
  Clang module inputs and triggered 42,454 tasks; keep that physical path stable
  during any further reviewed source migrations.
  The full candidate has not completed yet; consult `overnight-monitor.json` for
  the current resume run ID.

Both use runner `he3als-pc-container` in Docker container `discord-electron-runner`.
The container has all 20 logical CPUs available, a 26 GiB RAM limit and 36 GiB
RAM-plus-swap limit. Both dispatches select 16 tasks. No Blacksmith compiler is
running or queued. The last reported Blacksmith allowance use remains 168 minutes.

```sh
tail -f /mnt/storage/discord-patched-electron-ci/actions/discord-patched-electron/discord-patched-electron/out/capacity/build.log
gh run view 40036898682 --repo discord-patched-electron/discord-patched-electron
gh run view 40050257141 --repo discord-patched-electron/discord-patched-electron
docker stats discord-electron-runner
```

The log path is shared by successive Actions checkouts; check the run ID before
interpreting it. Ninja's resumed-task counter is not a percentage of elapsed time.
Leave the PC/container running. No terminal must remain open. Do not restart a
healthy compiler or delete its persistent build cache. See [live runner controls](ci-setup.md#changing-local-limits-while-the-build-runs).

## Available reference test build

```sh
/mnt/storage/discord-patched-electron-ci/local-tests/reference-canary-40036898682/launch.sh
```

This is a working local Canary build using the compiled reference Electron
44.0.0-reference.1, not the pending Electron 46 prerelease. It uses a separate
profile and passed the real login-form check. The [compiled-reference evidence](evidence/reference-runtime-local.json)
contains metadata only. Camera timing was 8.36 seconds upstream and 2.33 seconds
with the helper; no frames were displayed, saved or uploaded.

A one-shot local process (`run-overnight-smoke.py` under the CI storage directory)
waits for the new candidate, then fetches/verifies it and runs Canary assembly,
graphics, native/Krisp and login checks. Inspect `overnight-smoke.json` and the
live process before duplicating this work. It does not open a camera or hibernate.
The scheduled monitor and current run IDs are in `overnight-monitor.json` there.

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

The reference also passes four synthetic frame-capture lifetime cases under
native Wayland and Xvfb, including forced GC and window destruction. The new
prerelease GC port still needs this runtime check.

The dependency port and toolchain fix pass 73 Python tests and source reconstruction; the audio
adapters pass 12 JavaScript tests. CI checks use standard GitHub runners. Native
video/offline Krisp and audio CI baselines use Discord's bundled runtimes until a
successful compiler artifact triggers the custom-runtime jobs.

## Completion sequence

1. Reference compile, native and audio CI have all passed. Diagnose
   concrete failures before resuming; a quiet log is not itself a failure.
2. Let the prerelease compiler finish in the prepared workspace. Its identity
   is `f6a6a79b20ad975b5e64ad64d45df92dd007d514785f46cd66cd58baed91a390` under
   `/mnt/storage/discord-patched-electron-ci/build-cache/prerelease/`.
3. Fetch the successful artifact using `scripts/fetch_reference.py`. Build the
   actual Canary folder with `scripts/build_local_app.py --artifact ...`, then
   run `scripts/check_desktop_launch.py`; see [exact commands](linux-runtime.md).
4. Check frame-capture lifetime with `scripts/check_frame_capture.py`, native
   video/Krisp, isolated Wayland audio, local graphics and physical
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
