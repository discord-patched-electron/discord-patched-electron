# Capture and settings fixes, 8 November 2026

- Rust AV1 sends the source crop with every padded frame; its receiver accepts
  explicit metadata when the sequence header arrives through decoder configuration.
- Experimental vkcapture cursor acquisition/composition is disconnected. Shared
  cursor rendering remains available for future repair; game capture is documented
  as having no cursor support.
- Audio routing and application choices exclude DPE processes across versions and
  profiles using their inherited launcher environment and process ancestry. The
  common route covers manual system audio and automatic KMS/PipeWire selections.
- AppImage permission setup stages its bounded installer inputs outside FUSE.
  Privileged code reads the mounted runtime as its owner, preserving inode/time
  grants; native verification changes only its thread filesystem UID while reading.
- Settings keep sidebar/content scrolling within the window at short/narrow sizes.
  Outdated grants show the direct capture switch off; re-enabling requests approval.

Validation: tests and desktop harnesses were deliberately not run at the user's
request. End-to-end verification is left to the human operator. Repository lint
includes compilation through Clippy, not execution of Rust tests.

## Permission failure diagnostics

The reported settings failure occurred after polkit accepted the installer launch.
Read-only inspection of the managed `capture-fixes` inputs confirmed that its
payload, executable binding and maintenance inputs were readable as the user.
The installer previously discarded exception details, and the settings bridge
ignored its captured stderr.

Installer failures now include the exception type and message on stderr. The
settings bridge records bounded stderr, exit status and permission state in the
profile's `logs/dpe/runtime.log`, without recording the command or authorization
socket token. The active dev watchers staged these changes. A fresh explicit
enable attempt is needed to identify the privileged failure; no root permission
changes were made during diagnosis. Repository lint and whitespace checks passed;
tests and desktop capture checks were not run.

### Confirmed privileged failure and correction

The user's retry recorded `PermissionError: [Errno 13] Permission denied` for
Discord's `/proc/<pid>/exe`. The AppImage input context used `seteuid`, which
removed the installer's effective capabilities and blocked that procfs read.
It now uses thread-local `setfsuid`, matching the native KMS permission reader,
while retaining effective root credentials. Both switching and restoration are
verified because `setfsuid` does not report failures normally. Nested contexts
restore the exact prior filesystem identity before privileged writes.

This preserves AppImage filesystem access and the existing live executable,
owner, inode, timestamps and ELF checks. Added regressions cover nested/error
restoration and silent switch failure; they were not run at the user's request.
See [setfsuid](https://www.man7.org/linux/man-pages/man2/setfsuid.2.html) and
[capability effects of UID changes](https://www.man7.org/linux/man-pages/man7/capabilities.7.html).
The dev watchers apply the helper change; a further explicit enable action is
required to verify real authorization. No system grants were changed by the agent.

## Other DPE instances in system audio

Read-only inspection of the two running dev profiles showed that neither main
process nor its audio worker exposed `DPE_APP_ROOT` in `/proc/<pid>/environ`.
The previous environment-only cross-instance exclusion therefore returned false.

The audio helper now identifies published DPE runtimes from their adjacent camera
preload sidecar, and portable/cached runtimes from the application sidecars behind
their resource link. It follows worker ancestry and retains environment detection
as another signal. The existing route filter applies this to PipeWire and real
PulseAudio playback, including manually selected system audio and automatic
monitor audio for KMS/PipeWire. The application picker uses the same exclusion.

After watcher staging, a read-only process check identified both running dev
main processes as excluded; the separate browser remained eligible. Regression
cases were added for environment-free published profiles, resource-linked runtimes
and worker ancestry. Repository lint and whitespace checks passed. Tests and live
media capture were not run; human audio-isolation verification remains outstanding.

## Hardware AV1 viewer runtime mismatch

The reported viewer was using Electron hardware decoding at 1920×1082. Its
managed `capture-fixes` build reuses Electron 46.0.0-alpha.1, runtime key
`dfe843643db999ec0d6f392412deeb7ce73ddc0118f879e5601089de61afbdde`,
compiled before the hardware decoder crop support. Its recorded source follow-up
patches do not include `av1-crop-metadata.patch`. The Rust sender changes alone
cannot add that parser to the already compiled Electron receiver.

Created managed version `capture-crop` from the current tree and client lock,
using the retained Electron 46.0.0-alpha.6 artifact with runtime key
`9ce794464fc2288eb95783be29deb2c7fc5d0c57c2d71aa48a68bc56669d79eb`.
Its compiler record includes the crop decoder patch. Capacity, local provenance
and runtime archive hashes were checked against `config/local-runtime.json`;
normal artifact validation accepted them. Truthful local artifact provenance was
staged in `out/prerelease-import-validation/artifact-provenance.json` for the
existing builder. Current helper compilation and managed assembly completed.
No Electron compilation or release-policy change was made.

Stop the old managers, then run `capture-crop` with the existing `dev` and `other`
profiles in separate terminals. Both sender and viewer should use this version.
The old version and all profiles remain intact. Repository lint and whitespace
checks passed. Tests, desktop launch and live AV1 viewing were not run; displayed
crop geometry still needs the user's human verification.

## Working-tree publication checks

The Mods tab now displays project logos without rounded clipping and with a
transparent CSS background. Original PNG transparency is preserved. The real
settings harness rendered the Mods tab and passed its install, uninstall,
failed-switch and focus checks. Full harness completion remains unverified:
Wayland timed out waiting for the theme editor to grow; an explicit X11/Xvfb
retry failed the harness's expected 1100×800 window-size assertion. Rendered
Mods evidence is in `out/mod-icons-unrounded-20261108/mods.png`.

Publication checks exposed stale fixtures: maintenance now needs an `as_user`
context, the audio executable mock must let real resource symlinks resolve, and
outdated capture grants remain configured while reporting capture disabled.
The fixtures now cover those contracts. Focused permission, maintenance and
Wayland audio tests pass. Release integrity/provenance verification also passed
for `v20261108024415.0.0-canary.aff82962c976622d`; that existing release predates
these working-tree changes.
