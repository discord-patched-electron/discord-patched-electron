# Independent state reliability verification

Verified 2026-10-31 for the 20261030 review against unchanged
`f90b251caee6b4e7d265c0e450e53a70d97b0aba`.

| Case | Verdict             | Own severity | Established effect                                                                                                                                                                             |
| ---- | ------------------- | ------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| S1   | CONFIRMED           | Medium       | Explicit offline client selection rejects a complete, package-pinned generation without its current pointer. Normal mode can recover without successful network access.                        |
| S2   | CONFIRMED           | Medium       | A cached Electron file with all execute bits removed passes validation and reaches the bootstrap return boundary without repair.                                                               |
| S3   | CONFIRMED, narrowly | Low          | The loader forgets an independently surviving second instance and selects pending application code at the third launch. Experimental multi-instance scope; no demonstrated crash or data loss. |

The severity proposed for S3 in the blind claims is downgraded. None of these
tests establishes destructive data loss, an Electron crash, or an approval bypass.
S1 is not permanent rejection of an interrupted application update. S3 is not
generation or backup garbage collection. Both known exclusions remain excluded.

## Independence, safety and production baseline

Read `CONTRIBUTING.md`, this directory's index and the shared checklist. Initial
claim evidence was restricted to `state/blind-claims.md` and
`state/test_repro_state_reliability.py`. The original reviewer report/reasoning
and other reviewers' artifacts were not consulted. No subagents were used.

Copied the supplied test byte-for-byte into
[test_repro_state_reliability.py](../../tests/test_repro_state_reliability.py).
Added separate counterchecks in
[test_verify_state_controls.py](../../tests/test_verify_state_controls.py).
All candidate execution boundaries are mocked: no candidate Electron or
application payload is executed. The fixtures generate disposable keys and verify
real signatures. Module construction is synthetic; host integrity/authorization,
runtime integrity/selection and loader selection are production implementations.
The application fixture executes a disposable copy of the repository's loader for
resolution/staging, not its application payload. The full existing test suite also
executes its own synthetic scripts/native fixtures.

All dependency setup, Python execution, tests, formatting and lint ran under
`unshare --user --map-current-user --net env UV_OFFLINE=1`. Python used `uv run`;
dependency setup used `uv sync --locked --offline`. Existing `node_modules` was
symlinked for tooling and used without installation or writes. No downloads,
real profiles/accounts, host devices, external services, Git remotes, CI or
publication were used. No production, pinned upstream, patch or lock file changed.

`unchanged-production.log` records HEAD, an empty tracked diff (index and working
tree against HEAD), untracked artifact paths, and matching SHA-256 hashes for the
supplied original/copy. These checks cover **all tracked files**, not only selected
production directories. Added tests and this report are untracked review artifacts.

## S1: completed client generation lacks its pointer

**Trigger and observation.** During first client preparation, interruption occurs
after generation replacement and validation but before writing `current.json`.
The exception is a `BaseException` outside the updater's handled failures, modelling
process termination at that exact boundary. Locks/context managers unwind in the
fixture; ordinary process exit also releases the advisory lock. This demonstrates a
process-interruption window, not physical power-loss durability.

The generation passes `validate_host`, has package approval and exact package module
pins, and all required synthetic modules are complete. An offline retry raises
`First launch requires an internet connection; reconnect to repair an unapproved cached client`.
An otherwise identical normal-mode retry selects the same generation with module
installation and downloads forbidden. A later offline retry then succeeds.

**Independent attempt to refute.** The additional S1 control removes the special
`DPE_CLIENT_PINNED=1` setting for retry and makes approval download raise
`OSError("synthetic offline")`. Normal mode still repairs the pointer, with module
installation and patch prerequisites forbidden. Its only diagnostic is
`Approved client update unavailable; retaining pinned modules: synthetic offline`.
Thus real connectivity is unnecessary; the problem is explicit offline mode, not
all launches on a disconnected machine. Quickstart also does not create a permanent
trap: [startup.cjs](../../integration/updater/startup.cjs), lines 61–65 and 96–98,
requires an existing pointer for Quickstart and falls back to normal preparation.

**Source.** [client.py](../../integration/updater/client.py), lines 140–155 and
185–192, authorize package pins offline. Lines 415–419 look only at `current.json`;
452–457 reject without checking a complete generation when that pointer is absent.
Lines 495–522 publish/validate the generation before publishing the pointer; the
normal-mode branch can reuse it. Selection, host validation and exact package-pin
authorization are not mocked; module payload construction is synthetic.

**Intended limitations.** [Client updater documentation](client-updater.md),
lines 91–95, intentionally requires package/approved pins and permits rejection of
older unapproved caches. [Launcher documentation](../linux-launcher.md), lines
105–106, requires an existing verified installation for offline mode. Those are
real constraints: this is an interrupted first setup, not a previously launched
installation. Nevertheless the cache here is complete and currently authorized;
the error's unapproved/network diagnosis is false. No comment or documentation
specifically excludes offline recovery of this complete pinned generation.
The stronger interruption promise at client-updater.md:43–48 concerns the
**application** loader and must not be treated as a direct client-module guarantee.
The legacy-cache comment at client.py:328 likewise is not a first-setup guarantee.

**Impact/severity.** Medium, recoverable interruption to explicitly offline setup.
Disable explicit offline mode and retry to recover the existing bytes, even without
connectivity. No permanent rejection, lost cache, mandatory redownload or data loss
is established. The interruption window is narrow. Suggested direction: before
offline rejection, discover and fully validate a compatible exact authorized
generation and republish its pointer without downloads; retain all approval gates.

## S2: lost runtime execute permission is accepted

**Trigger and observation.** Seed a runtime-free synthetic package from its pinned
local archive, then change only its cached `electron` mode from executable to
`0600`. This models a restore/copy that loses Unix execute bits; the updater is not
claimed to cause permission loss. The file bytes and hashes are unchanged.
`validate_generation` accepts it. Offline, bootstrap and ordinary selection return
that file, `os.access(..., os.X_OK)` remains false, and `install_runtime` is not called.
The real `bootstrap.prepare` returns the same path without starting its helper.

**Independent attempt to refute.** The original test mocks `probe` throughout,
which might hide a later detecting probe. The independent control mocks it only
for safe initial seeding, then replaces it with an assertion failure: it is never
called on retry. Enabling ordinary update checks and returning the same pinned
release still does not repair. Even passing the known-good local archive with
`offline=True` does not force repair while this cache is considered current.
Restoring `0700` alone restores executability without changing bytes or selection.

**Source.** [runtime.py](../../integration/updater/runtime.py), lines 290–305,
checks file presence, resources and hashes, but no execute permission. Lines
317–322 select the accepted current generation. Lines 473–476 return it early for
offline/bootstrap, before considering a supplied archive; lines 503–504 retain it
on normal same-runtime selection. Probe/repair at lines 370–378 is inside
`install_runtime`, which these returns bypass.
[bootstrap.py](../../integration/updater/bootstrap.py), lines 18–23, returns that
offline selection immediately. [discord-launcher.sh](../../integration/launcher/discord-launcher.sh),
lines 62–73, uses it to start the startup UI and propagates execution failure.

**Intended limitations.** [Client updater documentation](client-updater.md),
lines 110–119, promises damaged-cache repair and requires an intact offline cache.
This does not promise arbitrary profile-restore compatibility or offline recovery
without replacement bytes. However, a non-executable file is not an intact usable
runtime; accepting it avoids even diagnosis/repair. The reuse comment at
runtime.py:327–328 discusses archive identity, not bypassing mode validity.
The bootstrap comment at bootstrap.py:24–25 intentionally optimizes cached starts,
but does not state that permission checks are excluded. The application loader's
missing-launcher-permission recovery documented at client-updater.md:334–340 is a
different component and does not repair this Electron file.

**Impact/severity.** Medium, recoverable launch obstruction after external metadata
damage. Returning a non-executable path and bypassing preparation are measured;
the launcher's inability to execute that file follows from the mode and call site.
No actual Electron launch/crash or user-data effect was tested. No general damage
from ordinary updater operation is established. Suggested direction: include
executable usability in cached validation and route failure through repair or a
clear offline error; consider safe permission restoration only after verification.

## S3: a surviving second instance is absent from running state

**Trigger and observation.** First launch becomes ready. A second independent
`--multi-instance` launch selects its generation and has no trial readiness token.
Stage a newer signed, approved application. Remove only the first PID from the
synthetic PID/start-time map. A third launch selects the new application at the
mocked `execve` boundary while the second old PID remains alive. `running` is
replaced by the third PID. This is selection/attempted activation, not completion
of the new application's readiness trial.

**Independent attempt to refute.** The supplied test directly writes `pending`
after real install/approval. The separate control uses the actual disposable
loader's `--stage` path instead and obtains the same result. Before first-process
exit, a third selection returns the old app with no trial and leaves state unchanged;
exiting only the second process also leaves the old selection protected. Only
exiting the tracked first process permits new selection despite the survivor.
The old application still passes full generation validation afterward: its
immutable files were not overwritten or deleted.

**Source.** [loader.py](../../integration/bootstrap/loader.py), lines 296–301,
uses PID and start time; this identity function alone is modelled. Lines 345–349
complete the first readiness trial. Lines 360–371 check a single running record
and return without registering a second launch. Lines 435–440 replace that single
record. The argv/exec boundary is lines 503–504.

**Experimental policy and intended limitations.**
[startup.cjs](../../integration/updater/startup.cjs), lines 44–49, explicitly bypasses
the failed single-instance lock for either the saved preference or the switch.
[Pinned settings UI](../../integration/updater/openasar/config.html), lines
241–243 and 256, labels Multi Instance **Experimental**, defaults it off and
describes multiple simultaneous windows/instances. This source was read, not
modified. [Updater history](updater-history.md), lines 343–347, records intentional
preference preservation. [README](../../README.md), line 7, also warns that the
project is experimental.

The loader comment at lines 368–369 explicitly assumes a second launch forwards
to the first. That is reasonable for ordinary single-instance mode and explains
why it does not register the second PID. It does not model the exposed opt-in
mode's independently surviving process. [Current updater docs](client-updater.md),
lines 59–66, state that second launches retain the running generation to avoid
activating beneath it, without a multi-instance exception. No explicit statement
was found that losing survivor tracking or mixed-generation activation is intended.
Experimental status substantially limits the guarantee and priority, but is not
evidence that this precise behavior is intentional.

**Impact/severity.** Low: confirmed tracking/activation-order gap in an experimental
opt-in mode. The fixture models a second process that remains alive; it does not run
Electron/OpenAsar to establish end-to-end concurrent client behavior. The observed
effect is selection of another approved immutable generation, not replacing the
survivor's files, violating approval, crashing, or losing settings. A medium or high
rating based on those unmeasured effects is unsupported. Suggested direction:
track all independently live instances with PID/start-time identity, or explicitly
define and enforce a narrower updater policy for multi-instance mode.

## Reproduction and validation artifacts

Working directory:
`/home/he3als/.t3/worktrees/discord-patched-electron/t3-db873a50`.

Artifact directory:
`/home/he3als/scratch-ai-repo/discord-patched-electron/out/reliability-review-20261030/verify-state/`.

Run from that worktree after copying the supplied test into `tests/`:

```sh
unshare --user --map-current-user --net env UV_OFFLINE=1 uv sync --locked --offline
unshare --user --map-current-user --net env UV_OFFLINE=1 uv run python -m unittest discover -s tests -p test_repro_state_reliability.py -v
unshare --user --map-current-user --net env UV_OFFLINE=1 uv run python -m unittest discover -s tests -p test_verify_state_controls.py -v
unshare --user --map-current-user --net env UV_OFFLINE=1 bun run test
unshare --user --map-current-user --net env UV_OFFLINE=1 bun run lint
```

All three supplied reproductions and all three independent controls pass.
`reproduction.log` and `controls.log` contain the complete output. These tests
intentionally assert the baseline behavior, including defects; passing is evidence
of reproduction, not a fix. `full-test.log` records the initial full suite (344
Python tests, 75 JavaScript tests). `full-test-final.log` records the final suite
including controls (347 Python tests, 75 JavaScript tests), all passing.
`lint.log` records the final passing lint. The initial `lint-initial.log` records
only a formatting failure in this newly added report, corrected before rerunning.
`uv-sync.log` records offline setup.
`commands.log` records exact commands and exit statuses for final checks.
`unchanged-production.log` records baseline proof. The artifact directory also
contains byte-identical copies of both tests and this report (`report.md`).
