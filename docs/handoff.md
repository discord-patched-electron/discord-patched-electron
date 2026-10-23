# Handoff and build monitoring

The Wayland audio implementation is saved. Keep the existing reference compiler
running; do not restart it to update the agent checkout. Its Ninja objects and
compiler cache are valuable. No additional Blacksmith compiler was started after
the maintainer's confirmed 168 allowance minutes.

## Watch the PC build

[Reference build 40005436647](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/40005436647)
uses runner `he3als-pc-container` in Docker container `discord-electron-runner`.
Expand **compile → Build the locked reference fork**.

```sh
tail -f /mnt/storage/discord-patched-electron-ci/actions/discord-patched-electron/discord-patched-electron/out/capacity/build.log
```

Or query GitHub:

```sh
gh run view 40005436647 --repo discord-patched-electron/discord-patched-electron
```

A moving `[completed/45993]` counter means Ninja is progressing; it is not a linear
time estimate. A quiet log alone does not establish a hang. Leave the PC and
runner container running. No terminal must remain open. Send the run URL and
success/failure status when returning; the final 30–50 log lines help on failure.
Do not delete the cache mount or repeatedly rerun a failed build before diagnosis.

The reference checkout retains its original CPU flags. Published candidate builds
use the approved x86-64-v3 baseline. See [CPU and cache settings](build-caching.md).
Successful compiler runs automatically trigger native video/Krisp and application
audio validation using the completed artifact, without compiling it again.

## Saved audio work

[Wayland implementation](wayland-audio.md) documents the native picker hooks,
exact niri/PipeWire source matching, private multi-process audio mix, PulseAudio
compatibility, explicit selector on other compositors, and crash cleanup.
Wayland routing is now enabled by default; `DPE_APP_AUDIO=0` disables it. The remote UI and native video path remain intact.
The fixtures run without an account, network, physical microphone or host sockets.
They are not a real Discord call or a completed custom-runtime qualification.

[Checks 40030377851](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/40030377851)
passed for audio implementation `d6ad1ab`: 58 Python tests, ten JavaScript tests,
source assembly, compiler-cache checks, native video/Krisp, all nine package
recipes and the Wayland/X11 audio fixtures. Wayland runs Stable/PTB/Canary against
both PipeWire-Pulse and real PulseAudio, plus a real audio-picker interaction case.
[Package validation 40029330180](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/40029330180)
checks the subsequent explicit `pipewire-audio` dependency at `07b2eb2`.
These workflows use standard GitHub runners, not Blacksmith or the PC compiler.
See [retained audio reports](evidence/wayland-audio-ci.json) and
[implementation status](status.md) for the final results and remaining gates.

## Resume after the compiler completes

1. Inspect the reference compile result and automatic validation artifacts.
2. If it failed, fix the concrete compiler failure and resume its retained
   workspace. An observation timeout does not itself mean compilation failed.
3. If it passed, inspect disk/memory/cache evidence and build the current prerelease
   x86-64-v3 candidate on the existing runner.
4. Run a real native Wayland share with the new runtime and microphone/Krisp
   active. Qualify actual GNOME/KDE sessions and sandboxed apps separately;
   automatic identity is currently niri-specific.
5. Finish queue-to-compiler reconciliation, the release ledger and automatic AUR
   publication after runtime qualification. Discovery already preserves every
   prerelease and skips unchanged inputs. Configure AUR credentials privately
   when the publisher is concrete and reviewable.

No binary release or AUR update has been published. The three prerelease-Electron recipes exist, but
the compile/release/AUR coordinator is still unfinished and no AUR key is configured.

## Camera and local controls

The [camera fix](camera-startup.md) was verified on the physical webcam with the
current installed Canary module and bundled Electron, independently of the active
compiler. The helper is compiled separately on standard GitHub Actions and supplied
as a checksum-pinned binary to makepkg; its source does not invalidate Electron
objects. The release publisher must upload the helper's content-addressed asset
before publishing recipes referencing it, alongside its existing runtime gates.
[Live runner controls](ci-setup.md#changing-local-limits-while-the-build-runs) use
`docker update`; both compile workflows accept `parallel_jobs` on the next dispatch.

The follow-up local checks pass: 61 Python tests, 11 JavaScript tests, actionlint,
three Stable-client Arch packages, physical-camera A/B measurements, default-on
Wayland routing on both audio servers and manually operated audio dialogs.
`docs/evidence/camera-wayland-local.json` retains these results. Full GitHub
validation for this follow-up is separate from the earlier green CI run above.

## Scope and scheduling update

The maintainer now wants prerelease Electron only, retaining Discord Stable, PTB
and Canary. Old stable/nightly pending builds are retired without cancelling any
running work. Future dispatches default to resource-aware auto parallelism (four
Ninja tasks with the current limits) and one linker at a time. The current
reference run and its cached workspace are untouched. See `build-caching.md`.

[Camera/audio follow-up CI 40034425231](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/40034425231)
passed all five jobs at `1552ff6`, including all then-enabled package combinations.
