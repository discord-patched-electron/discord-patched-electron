# Independent snapshot review: state and audio, 31 October 2026

No actionable regression was confirmed in the scoped snapshot changes. The five
claimed corrections pass the supplied regression tests and the independent
controls described below. This is an offline fixture review, not live Discord
or audio qualification. No production fixes were made by this reviewer.

## Candidate and isolation

- Checkout: `/home/he3als/.t3/worktrees/discord-patched-electron/t3-0539c28c`.
- Confirmed HEAD: `f90b251caee6b4e7d265c0e450e53a70d97b0aba`.
- Snapshot: `fixes-review-snapshot.zip`, SHA256
  `8f1d8e60afa105bf2b67771f0e6c3479aa70d5e21ac0b6c7139035529f03d166`.
- Verified all 43 entry hashes against the adjacent JSON manifest before a single
  overlay into this prepared checkout. Reviewed the source diff against HEAD.
- Read `CONTRIBUTING.md`, `docs/agent/README.md`, current policy comments,
  `docs/linux-launcher.md`, `docs/wayland-audio.md`, and current design sections in
  `docs/agent/client-updater.md` and `docs/agent/wayland-audio.md`. Historical
  investigation reports were not used as evidence.
- The original dirty repository was neither read nor written. No network,
  account operations, real installation, live profile, device capture, production
  credential, paid CI, or runtime compilation was used. GPG keys were disposable.
- Dependencies used `uv sync --locked --offline` and the authorized shared
  `node_modules` symlink. Tests ran as the current UID in a new network namespace.

## Reviewed behavior and evidence

1. **Offline client recovery**, `integration/updater/client.py:443–471`.
   Recovery derives generation identities from authorized sources and the current
   compatibility fingerprint, then validates the complete generation before
   publishing its pointer. Supplied tests exercise interrupted publication,
   compatibility/pin/module damage, and actual loader rollback with mocked exec.
   The new independent signed-cache fixture removes the client pointer, recovers
   using real disposable-key signature verification, then rejects damaged
   `approval.json`, damaged `SHA256SUMS.sig`, and a release newer than the selected
   application. Restoring evidence restores recovery. Download and installation
   boundaries raise if called. Cached payload and proof bytes remain unchanged.
   Another independent matrix checks missing-generation, null, array, and
   malformed pointers while preserving a separate complete approved generation.

2. **Runtime executability**, `integration/updater/runtime.py:290–306`.
   The added `os.access(..., os.X_OK)` excludes a byte-intact Electron file with
   removed execute permission. Supplied controls cover rejection before bootstrap
   starts a helper and repair from local archive or fixture download. The new
   cross-application control also rejects a non-executable sibling cache without
   probing it, then reuses the same bytes once executable permission is restored.
   Source bytes and the running application's resource link remain unchanged;
   the new runtime receives its own resource link.

3. **Surviving instances**, `integration/bootstrap/loader.py:313–359,373–389`.
   Registration retains each live PID/start-time pair, and selection revalidates
   the retained generation through the existing authorization/file-index gate.
   Supplied controls exercise a surviving second instance, real stage handling,
   trial-peer registration, malformed records, and activation only after other
   instances exit or their PIDs are reused. The independent control uses the
   production `/proc/stat` parser with synthetic parenthesized process names:
   reparenting preserves both instances; zombies, dead processes, absent PIDs,
   and changed start times do not keep a generation pinned.

4. **Wayland reparenting**, `integration/audio/pipewire_audio.py:628–659`.
   Stable target identity is PID/start time; current parentage is still passed to
   route synchronization. Supplied fixtures cover niri graph/cast correlation,
   actual `Route.sync` with synthetic children, parser-level UID/start changes,
   and cleanup. The independent native-hint path preserves its route after a
   parent-only change, passes updated identity to synchronization, closes once
   after PID reuse, stays unavailable across repeated ticks, and resumes only
   after an explicit new generation. Final close releases the replacement route.

5. **Native source rejection**, `integration/audio/app-audio.cjs:155–187,209–219`.
   A new source is committed after the native setter returns. Exceptions clear
   source, requested PID and hint and stop the timer/transport before rethrowing.
   Supplied fixtures cover switching sources and the actual resolver against
   synthetic X11 and process data. Four independent cases cover both setter APIs
   and window/screen sources on the same-source fast path. They verify exception
   identity, zero requested capture/loopback, timer removal, successful return
   value preservation, ordinary transport option forwarding, and fresh audio
   consent after a successful video retry. Destroy clears the remaining timer.

## Independent fixtures and commands

Added only these test files and this report beyond the immutable overlay:

- [Python controls](../../tests/test_review_state_audio_snapshot.py): five tests.
- [JavaScript controls](../../tests/audio/review_snapshot_rejection.test.cjs): four tests.

All test commands used the prefix:

```sh
unshare --user --map-current-user --net env UV_OFFLINE=1
```

Commands after that prefix:

```sh
uv run python -m unittest discover -s tests -p test_review_state_audio_snapshot.py -v
node --test tests/audio/review_snapshot_rejection.test.cjs
env PYTHONPATH=tests uv run python -m unittest -v test_application_updates test_client_updater test_runtime_updater test_runtime_bootstrap test_wayland_audio test_repro_state_reliability test_verify_state_controls test_verify_approval_rollback test_repro_audio_reparent test_verify_audio_reparent test_review_state_audio_snapshot
node --test tests/audio/*.test.cjs
bun run test
bun run lint
```

## Validation

| Run                                            | Result                                    |
| ---------------------------------------------- | ----------------------------------------- |
| Unmodified snapshot full suite                 | 368 Python and 86 JavaScript tests passed |
| Independent controls                           | 5 Python and 4 JavaScript tests passed    |
| Affected suites including independent controls | 102 Python and 28 JavaScript tests passed |
| Full suite including independent controls      | 373 Python and 90 JavaScript tests passed |
| `bun run lint`                                 | Passed                                    |

Counts overlap; they are not additive coverage. No tests were skipped in these
successful runs.

The first independent Python run had one fixture error: the inherited signed
manifest omitted the `application` descriptor, so the explicitly
application-dependent version gate did not apply. Adding a signed synthetic
descriptor exercised the intended policy and passed. This was not a product
failure. The initial log is retained as
`independent-python-initial-fixture-error.log`.

Report, independent fixture copies and logs are saved under:

```text
/home/he3als/.t3/worktrees/discord-patched-electron/review-offline-reliability-20261030/out/project-reliability/verify-fixes-state-audio/
```

## Coverage limits and disposition

No live Electron multi-instance process, Discord call, X server, compositor,
PipeWire/PulseAudio session, device, actual PCM output, or physical capture was
exercised. Tests establish state transitions and native-boundary arguments;
they do not establish actual live leakage, absence of leakage, or crash behavior.
No such claim is made. Process races outside the deterministic sequences and
resource exhaustion are not qualified by this run. No package or native audio
integration build was run.

Recovery remains bounded to package pins and retained approvals found by current
source selection, and to the current compatibility fingerprint when rebuilding
a missing pointer. It does not promise recovery of every orphan directory or
automatic repair without usable local bytes. Legacy module markers and the
existing valid-current-host offline fast path retain their prior policy. The
runtime change validates downloaded generations, not all packaged-runtime
fallback paths. Reparenting coverage concerns the selected application, not the
helper's native owner lifetime. These are scope limits, not demonstrated new
regressions.

Permanent rejection of interrupted update launches and uncollected
generation/backup directories were excluded as requested. No speculative issue
is promoted to a finding. The coordinator can proceed on the scoped offline
evidence, with live integration qualification still outstanding.
