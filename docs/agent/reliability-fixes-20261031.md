# Reliability fixes, 31 October 2026

This follow-up fixes the four confirmed findings from the
[offline review](reliability-review-20261030.md), plus the conditional X11
exception-handling gap. No production service, account or installed profile was
used. The two originally excluded issues remain outside this change.

- **Audio identity:** Wayland replacement detection uses PID/start time rather
  than mutable parentage. Current ancestry remains available for routing; UID
  changes and genuine PID reuse still silence capture.
- **Runtime usability:** cached Electron without execute permission fails
  validation. Online preparation repairs it through the verified archive path;
  offline preparation accepts a supplied verified archive or fails clearly.
  Validation does not silently chmod an unverified file.
- **Offline client recovery:** after an absent, malformed or unauthorized current
  pointer, selection derives candidate identities from verified approval/package
  sources and the current compatibility fingerprint. It validates existing modules
  and republishes the pointer without downloads or installation. Rollback never
  authorizes the newer application's client under an older application cap.
- **Instance tracking:** the loader retains its existing `running` record and
  adds validated `instances` records. Every launch is registered, including peers
  arriving during a trial. Exited processes, PID reuse and same-process handoffs
  are pruned; a surviving peer keeps the old generation selected. Legacy state
  without the list remains readable. The loader ships in packages, so deployment
  of this part requires a package upgrade, not only an application archive update.
- **Defensive X11 rejection handling:** both native setters silence the old
  selection before switching and activate the next audio selection only after
  acceptance. Exceptions clear selection, hints and timers, leave capture silent
  across later updates, and propagate the original error. The original review
  did not establish a realistic persistent native rejection or live audio leakage;
  this fix does not change that evidence limitation.

The review tests now assert intended behavior. Added controls reject mismatched
pins, changed compatibility and damaged modules during offline recovery; cover
malformed instance metadata, trial peers and PID reuse; and preserve silence
after X11 rejection. Existing signature, rollback, mod/settings, source-exclusion
and cleanup tests remain enabled. Original baseline reproduction copies and the
shareable review archive are retained unchanged under
`out/reliability-review-20261030/`.

Validation runs with networking disabled:

```sh
unshare --user --map-current-user --net env UV_OFFLINE=1 bun run test
unshare --user --map-current-user --net env UV_OFFLINE=1 uv run python -m unittest discover -s tests -p repro_approval_controls.py -v
unshare --user --map-current-user --net env UV_OFFLINE=1 uv run python tests/repro_installation_boundary_matrix.py
unshare --user --map-current-user --net env UV_OFFLINE=1 bun run lint
```

All checks passed: **353 Python tests, 80 JavaScript tests, eight approval
controls and 163 installation fault cases** (three matrix methods), plus lint.
An initial added pin-mismatch control reused the fixture's original hash; that
test setup was corrected to use a genuinely different hash before the final run.

Logs are under `out/reliability-fixes/`. Synthetic fixtures and mocked execution
boundaries establish the corrected state transitions; real calls, desktop audio,
physical power loss and deployed packages were not tested. No runtime compilation,
push, release or external publication was performed.
