# Optimized picker closure and native handoff, 4 November 2026

The user observed that choosing Java/vkcapture, then any Optimized monitor,
opened the desktop portal. These share one selection handoff. The React picker
cleanup sent `disarm` and `picker-close`; the main process's close handler also
cleared its armed source. Discord's asynchronous native request could therefore
arrive after cleanup, find no armed source, and take the ordinary portal path.

The UI now marks a committed Optimized start immediately before invoking the
original native start callback. Its cleanup preserves that one-shot selection
through an explicit `handoff` flag; ordinary dismissal still disarms it. The
renderer forwards only a strict boolean to the owner-checked IPC handler. The
main process retains the existing source/owner binding and 15-second expiry,
consumes the selection once, and still checks experimental capture/permission.
It does not suspend the game broker during a committed handoff, since doing so
can cancel the pending producer. Native/Vanilla selections retain the portal.
No native binary or Electron rebuild was required.

Regression tests unmount synchronously inside Discord's original start callback
for game and monitor selections. Backend tests close the picker before the
native request and check one-shot consumption, ordinary cancellation and game
broker continuity. These are behavior tests, not proof of exact visual parity.

The general settings harness was attempted and stopped at its unrelated Mods
hover-color assertion (0.12 instead of 0.16 alpha). The screensharing-only
harness then failed with a destroyed settings window before its first check.
These failures remain in `out/experimental-codecs-manual/handoff-settings*.log`
and `handoff-screensharing.log`; no settings harness success is claimed.
An initial harness setup also exposed the packaged runtime's relative resources
symlink; the retry used a standalone disposable runtime resources directory.
The original runtime and manual app resources were untouched by that setup.
