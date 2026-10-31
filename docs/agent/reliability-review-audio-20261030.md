# Offline audio reliability review — 30 October 2026

Baseline and final HEAD: `f90b251caee6b4e7d265c0e450e53a70d97b0aba`.
Prepared worktree: `/home/he3als/.t3/worktrees/discord-patched-electron/t3-cbd1971c`.
Production files remain unchanged. These are candidates for independent verification,
not independently verified final findings. No production fixes were made.

Evidence directory:
`/home/he3als/scratch-ai-repo/discord-patched-electron/out/reliability-review-20261030/audio/`.
For blind verification, supply only `blind-claims.md`, the two reproduction files,
and their reproduction logs; omit this reasoning report.

## A1 — Rejected X11 video source retains that source's audio

**Suggested severity: high (audio isolation); optional X11 adapter only.**
With `DPE_APP_AUDIO=1` on X11, share application A with audio enabled, then
attempt to switch to B. B's process may still exist while its window becomes
uncapturable between resolution and native source setup. If the native setter
throws, the wrapper has already enabled B's audio and never clears it. The
existing Wayland rejection test models this same native failure boundary.

**Expected:** silence on rejected native source selection, with no timer or
unrelated transport update restoring capture before a valid selection.
**Observed:** the error reaches the caller, but the last native transport PID is
B's PID, 200. A subsequent controller timer tick and unrelated bitrate update
both retain PID 200. Explicit `clearDesktopSource()` returns it to zero.
Both source setter variants reproduce this behavior.

Reproduction:
[repro_x11_rejected_source.test.cjs](../../tests/audio/repro_x11_rejected_source.test.cjs).
Its two **REPRODUCTION** tests PASS by asserting the wrong nonzero capture PID.
It reuses the native stub and two-window PID map from
[adapter.test.cjs](../../tests/audio/adapter.test.cjs), with timer and identity
boundaries mocked so the test never reads host processes. No native module or
capture service is started.

Original reasoning and correction proposal:

- `integration/audio/app-audio.cjs:155-174`: `select()` stops A, installs B in
  wrapper state, immediately updates native transport to B, and starts polling.
- `integration/audio/app-audio.cjs:196-204`: both setters call `select()` before
  the native setter, with no exception cleanup. Failure leaves the new state and
  timer active. `:128-149` and `:165-173` maintain that selection on later updates.
- Suggested correction: silence before native selection; activate the new audio
  selection only after native success. On rejection, invalidate selection/hints,
  cancel polling and preserve the original error. Merely sending PID zero once
  is insufficient if the timer can restore the rejected source.

Intentional-limitation check:
[the current Wayland guide](wayland-audio.md) lines 12-14 explicitly supports
opt-in X11. [The audio investigation](audio-investigation.md) lines 83-89
promises per-window identity and clearing capture on source transitions.
The one-audio-PID restriction in `app-audio.cjs:78-80` is respected here; each
application has one PID. There is no comment declaring native source rejection
an accepted exception. `wayland-audio.cjs:171-175` explicitly handles rejection
and says a rejected source must not leave an application audible.

Limit: this proves native transport selection at the mocked boundary, not
captured PCM, a live UI error rate, or how a particular native build displays
video after the exception. It does not claim default Wayland has this defect.

## A2 — Reparenting a live selected application disables audio until reselection

**Suggested severity: medium (recoverable loss of share audio).**
A normal niri application share can outlive the shell/launcher that started the
application. For example, a detached application continues running after its
launching shell exits. Its parent PID changes, while its own PID, start time,
UID eligibility, window and cast remain unchanged. The controller treats that
parent change as a replaced process and blocks the selected application.

**Expected:** retain the same live application's audio when only its parent
changes. **Observed:** a ready route containing the application's own stream and
its audio child's stream is closed; three subsequent ticks remain unavailable.
A new generation restores the same application and streams without any other
change. Unrelated application, Discord and helper-descendant streams are
excluded before and after the failure.

Reproduction:
[test_repro_audio_reparent.py](../../tests/test_repro_audio_reparent.py).
Its **REPRODUCTION** test PASSES by asserting unavailable state and no route
for the unchanged process identity. It captures and replays the exact existing
video-link and cast fixtures from
[test_wayland_audio.py](../../tests/test_wayland_audio.py); playback objects use
that fixture's existing node shape. Real `video_sources`, `niri_target`,
`descendant`, `Controller.tick`, `Route.sync` and route closure logic run.
Identities, graph/command/compositor replies, route start and child creation are
mocked. A subprocess guard rejects unexpected real process creation.

Original reasoning and correction proposal:

- `integration/audio/pipewire_audio.py:37-47`: process tuples contain
  `(pid, start_time, parent_pid)` after the same-UID eligibility check.
- `integration/audio/pipewire_audio.py:628-639`: the controller compares the
  **entire** tuple for a retained target key, so a changed parent closes the
  route and returns unavailable. The old key remains, repeating the failure.
- `integration/audio/pipewire_audio.py:52-61`: the existing ancestry helper
  correctly compares only PID/start time when identifying the same root.
- Suggested correction: use stable PID/start-time identity (with continuing
  ownership eligibility) for target lifetime comparisons. Retain current PPID
  separately for ancestry traversal. Preserve rejection of actual PID reuse.

Intentional-limitation check:
[the Wayland guide](wayland-audio.md) lines 25-29 describes checking UID and
process start time, and lines 47-51 promises all selected-process streams and
descendants. Neither lists stable PPID as a requirement. The comment at
`pipewire_audio.py:634-635` intentionally rejects vanished/reused processes;
this reproduction has neither. The manual-selection and Flatpak limitations
at guide lines 39-45 are inapplicable: this is the exact niri capture/window
relationship, without namespace remapping.

Limit: the process lifecycle is synthetic. No real application, compositor,
audio server, process inspection or PCM capture was used. The test checks niri
selection; it does not claim a real call or a complete manual-picker session.

## Validation, coverage and boundaries

Read CONTRIBUTING.md, docs/agent/README.md, the shared checklist, current audio
implementation/guide, historical audio investigation and relevant handoff
sections. Historical live checks were not rerun. Both excluded updater findings
were outside this review. Shared checklist/ledger and the original dirty checkout
were not edited; only the requested shared audio evidence directory was written.

Dependency setup: `unshare --user --map-current-user --net env UV_OFFLINE=1 uv sync --locked --offline`.
The existing original `node_modules` was symlinked for read-only dependency reuse;
no package install or writes to it were performed.

Commands, run from the prepared worktree:

```sh
unshare --user --map-current-user --net env UV_OFFLINE=1 uv run python -m unittest discover -s tests -p 'test_repro_audio_reparent.py' -v
unshare --user --map-current-user --net env UV_OFFLINE=1 node --test tests/audio/repro_x11_rejected_source.test.cjs
unshare --user --map-current-user --net env UV_OFFLINE=1 bun run test
unshare --user --map-current-user --net env UV_OFFLINE=1 bun run lint
```

Results: Python reproduction 1/1; JavaScript reproductions 2/2. Full test suite:
342 Python and 77 JavaScript tests pass (baseline was 341/75). Lint passes.
Logs: `repro-python.log`, `repro-js.log`, `full-test.log`, `lint.log` in the evidence
directory. `commands.txt` contains the working directory, dependency setup and
commands. `integrity.log` records HEAD and production diff checks.

Coverage includes source selection/switching, audio-off, source-ended and destroy
handling, helper exit/error and stale-generation rejection via the existing
JavaScript suites; exact niri link/cast correlation, ambiguity rejection, manual
picker cancellation, all-stream selection, and Pulse PID exit/reuse during sink
lookup via existing Python tests. New graph replay also exercises actual
ancestry filtering for an application's child and Discord/helper exclusions,
route child termination, reparenting and reselection recovery. X11 native
rejection tests assert persistence through both polling and transport updates.
Stale-hint handling was inspected in all three files; no additional candidate is
claimed from static inspection alone.

These tests do not qualify real audio-server timing, worker/kernel death signals,
Pulse module leases after SIGKILL, live portal event ordering, physical devices,
GNOME/KDE sessions, real call behavior or Flatpak namespaces. No production
services, installations, profiles, user data, real keys, network operations,
external Git operations, CI, releases or runtime compilations were used. Existing
unit-suite synthetic native fixtures run as part of the required test command.
