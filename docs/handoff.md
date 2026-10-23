# Handoff and build monitoring

The maintainer requested that coding be saved and agent work pause while the
reference build continues. Do not restart the compiler just to obtain a newer
agent checkout. Its existing Ninja objects are valuable.

## Watch the PC build

[Reference build run 40005436647](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/40005436647)
uses the `he3als-pc-container` runner in Docker container
`discord-electron-runner`. Expand **compile → Build the locked reference fork**.

On this PC:

```sh
tail -f /mnt/storage/discord-patched-electron-ci/actions/discord-patched-electron/discord-patched-electron/out/capacity/build.log
```

Or query GitHub without changing the job:

```sh
gh run view 40005436647 --repo discord-patched-electron/discord-patched-electron
```

A moving `[completed/45993]` counter means Ninja is making progress. It is not a
linear time estimate: some compile/link steps take much longer. A quiet log
alone does not establish a hang. Leave the PC and runner container running;
no terminal must stay open for the Actions runner to continue.

When returning, send the run URL and its success/failure status. For failure,
the last 30–50 lines are useful; the agent can fetch the complete diagnostics.
Do not delete the build-cache mount or rerun jobs repeatedly. A failed attempt
should be diagnosed before resuming the same incremental workspace.

The reference checkout intentionally retains its original CPU flags. Published
candidate builds are configured for the approved x86-64-v3 baseline. Compiler
cache/incremental settings and limits are documented in [build-caching.md](build-caching.md).
No additional Blacksmith compiler was started after the confirmed 168 allowance
minutes. Hourly discovery uses standard GitHub runners and does not yet dispatch
queued Electron compilations. Successful compiler runs automatically trigger
native bridge/Krisp and application-audio fixture workflows.

## Saved work and resume order

The repository now validates exact discovered client snapshots independently,
with PTB 1.0.224 and Canary 1.0.2357 promoted after bundled-native/Krisp and Arch
package CI passed. The opt-in application-audio wrapper and isolated X11 tests
are documented in [audio-investigation.md](audio-investigation.md). It is not a
complete Wayland/multi-process/live-call feature, and no binary release or AUR
update has been published.

**Wayland is primary, X11 secondary.** The maintainer clarified this before the
pause. The current session reports niri 26.04 (v26.04-214-ge9804c1c). Do not treat
the X11 fixture as completion of the requested audio fix. Trace-only module
instrumentation is now available independently of the audio correction; the
[Wayland work order](audio-investigation.md#wayland-first-work-order) documents
the exact remaining boundary and a promising niri IPC mapping. No live Discord
session was opened to collect this trace.

On resume:

1. Inspect the reference compile result and automatic validation artifacts.
2. If it failed, fix the concrete compiler failure and resume its retained
   workspace; do not infer failure from an observation timeout.
3. If it passed, inspect resource/cache evidence and validate the current stable
   candidate with x86-64-v3 using the same compiler runner.
4. Prioritize the live Wayland source-selection trace, then a private compositor/
   portal fixture. Establish multiple-process and microphone behavior before
   enabling application audio. Do not spend another round expanding X11 coverage
   in place of the primary Wayland path.
5. Finish queue-to-compiler reconciliation and the release/AUR publisher after
   the reference compatibility/free-capacity gates are established. Configure
   AUR credentials privately only when the publisher is concrete and reviewable.

No AUR credentials are currently configured. The nine package recipes and
packaging tests exist; discovery alone never authorizes publishing them.
