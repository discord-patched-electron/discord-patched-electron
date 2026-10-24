# Local Canary test build

Updated 24 October 2026. **Discord Canary 1.0.2357 with the compiled Electron
46.0.0-alpha.1 runtime opens successfully on native Wayland.** It requires an
x86-64-v3 CPU (AVX2; no AVX-512 requirement).

## Run it

```sh
/home/he3als/scratch-ai-repo/discord-patched-electron/out/canary-prerelease-test/launch.sh
```

The shortcut points to:

```text
/mnt/storage/discord-patched-electron-ci/local-tests/prerelease-canary-40053107555/launch.sh
```

Keep that installation directory in place: its launcher contains an absolute
path. It uses a separate profile at
`~/.config/discord-canary-prerelease-patched-electron-bin-local`, leaving the
stock Discord profile separate. Flags can be placed one per line in
`~/.config/discord-canary-prerelease-patched-electron-bin-flags.conf`.

Wayland selection is automatic. Application audio and the camera startup helper
are enabled by default. `DPE_APP_AUDIO=0` and `DPE_CAMERA_FAST_PROBE=0` disable the
respective adapters. niri supports automatic shared-window audio matching; other
compositors use the audio-app picker. See [Wayland audio](wayland-audio.md).

## Verified results

[Compiler run 40053107555](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/40053107555)
completed on the PC, using 20 compiler tasks at nice level 10 and one concurrent
linker. No Blacksmith compiler was used. The final resumed portion took 1,776
seconds; this is not the duration of a clean build.

The [retained evidence](evidence/prerelease-runtime-local.json) records:

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

The three prerelease-Electron recipes exist, but the automatic
compile/release/AUR coordinator is unfinished and no AUR publishing key is
configured. No public binary release or AUR update has been published. Hourly
release discovery continues to record changed inputs without compiling them.

Local monitor state is in
`/mnt/storage/discord-patched-electron-ci/overnight-monitor.json`.
All required tests have finished and the scheduled build monitor is disabled.
The user authorized hibernation after successful completion.
