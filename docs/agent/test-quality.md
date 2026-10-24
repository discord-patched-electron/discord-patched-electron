# Test quality review

Reviewed on 24 October 2026. This is evidence for the tested inputs, not a promise
that every future Discord, Electron, driver or desktop change will work.

## Findings and fixes

- Device-filter tests asserted inside callbacks without proving the callbacks ran.
  They now require exactly one delivery and check native receivers, extra arguments,
  return values and error propagation.
- Launcher tests inherited unrelated user overrides. Their subprocess environment
  now isolates app settings, and a run with conflicting host settings passes.
- Update-policy tests depended on the currently approved runtime and its completion
  date. Fixed policy fixtures keep normal release approval from breaking scheduling
  tests. Assertions also reject an empty work list instead of passing vacuously.
- Release preparation used an API mock that returned a dated release when asked
  for a legacy tag. Tests now model the actual lookup, pagination, draft retry and
  tag reservation paths, rejecting unexpected API requests.
- X11 audio analysis could accept unrelated audio during silent phases or hide a
  brief cross-application leak in a whole-recording FFT. It now checks short frames
  and peak silence. Wayland silence checks also reject brief bursts diluted by
  longer silence. The original X11 minimum remains 4,800 diagnostic samples.
- A missing silent-phase recording could mean either silence or a skipped test.
  Both native audio fixtures now record completed phases independently of PCM.
  Missing, duplicate and unexpected phases fail validation. X11 recordings flush
  into separate phase callbacks to avoid discarding or mislabelling late buffers.
- Malformed helper-message tests found a `null` JSON reply could throw. The adapter
  ignores non-object replies. Tests cover fragmented input, invalid PIDs, stale
  generations, helper failures and late replies after shutdown.
- Safe-storage and shortcut probes used Python `assert` as a release check. Explicit
  errors now preserve validation under `python -O`. Tests also check cleanup and
  prevent failed probes from writing an aggregate success report. The shortcut
  probe uses the shipped Canary app ID and fails clearly if its portal never starts.

## Verification

[GitHub checks](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/40133245248)
passed, including source assembly, native baselines, Arch packaging and the audio
matrix. Local unit tests, linting, shuffled test order and deliberate fault injection are
recorded in [the review evidence](../evidence/test-quality-review.json).
Seven isolated mutations were rejected: dropping device callbacks, exposing private
sinks, accepting stale helper generations, removing the null-message guard,
bypassing X11 silence checks, diluting a Wayland audio burst, and skipping signature
verification. Mutation tests ran in disposable copies, not the working tree.

The existing compiled runtime was reused for native video, offline Krisp, capture
lifetime, safe-storage relaunch, synthetic graphics playback, shortcut delivery,
X11 audio and Wayland audio with both server arrangements and manual selection.
Tests use synthetic content and private profiles. No physical camera test, account
login, message or call was performed. No camera images/video or desktop screenshots
were saved. No Electron compilation or new release was requested for this review.

The suite also covers source/import checksums, patch drift, archive extraction,
compiler reuse and resource budgets, channel selection, update scheduling, state
write races, package metadata, AUR revisions and real OpenPGP signature tampering.
Source-layout and checksum assertions intentionally fail when pinned upstream
inputs change incompatibly. Those checks should not be relaxed just to make an
update green.

## Remaining limits

Synthetic audio fixtures do not prove microphone continuity in a real Discord
call. The camera tests use a fake V4L2 driver. The portal fixture verifies native
registration and press/release delivery, not a real GNOME/KDE permission dialog.
Window metadata parsers have unit coverage, but each desktop still needs practical
qualification. Graphics results apply to the tested AMD GPU. Unsupported hardware
encoders are reported separately and software encoding can satisfy playback tests.
Automatic audio checks allow a small fraction of callback-boundary frames that
cannot be fitted cleanly. They are not proof against every possible audio signal.

When adding tests, include a positive control and a failure case that would have
passed the old implementation. Assert callbacks actually ran, isolate mutable
configuration, verify phase completion independently of empty output, and retain
useful diagnostics on failure. Avoid widening timeouts or weakening signal limits
without evidence that the fixture, rather than the product, is wrong.
