# APPROVAL-1 independent verification

**CONFIRMED, medium severity. Consolidate with S1 as one common defect.**

Reviewed HEAD: `f90b251caee6b4e7d265c0e450e53a70d97b0aba`.
Production code, existing tests, configuration, locks, pinned upstream files and
patches are unchanged. The initial worktree was clean. Only two reproduction
tests, this report and a dependency symlink were added. No fixes or commits.

The defect is failure to select an intact, authorized cached client when the
current client pointer cannot supply one in explicit offline mode. Application
rollback is a second supported trigger for S1, not an approval violation.

## Evidence boundary

Read `CONTRIBUTING.md`, `docs/agent/README.md` and the shared `checklist.md`.
Initial claim evidence was limited to `approval/blind-brief.md` and
`approval/test_repro_approval_reliability.py`. For duplication only, read
`state/blind-claims.md` and `state/test_repro_state_reliability.py`; executed only
its S1 method. No originating reviewer reasoning or report was read.

All execution used `unshare --user --map-current-user --net env UV_OFFLINE=1`.
Dependencies came from `uv sync --locked --offline` and a symlink to the existing
original-checkout `node_modules`; no dependency installation over that directory.
Tests used disposable profiles and signing keys. Added reproductions never execute
application, Electron or module payloads. No accounts, host installations, real
profiles, private keys, devices, services or external infrastructure were used.
The requested existing test suite was also run in the isolated namespace.

## Supported trigger and independent reproduction

The supplied test passes, but models application selection by replacing
`PACKAGE_ROOT` and `DPE_APPLICATION_VERSION`. It does not itself prove loader
activation, application archive validity or rollback. The added
[independent test](../../tests/test_verify_approval_rollback.py) closes that gap:

1. Create synthetic indexed application archives A and B with their own module
   pins, runtime policies and the disposable public key. A is pinned by a real
   generated bootstrap package. B has a real GPG signature covering both the
   manifest and archive hash and an exact matching approval record.
2. Invoke the actual loader `main()` with A's local archive, intercept `execve`,
   and prepare A's package-pinned client (`1.0.123`) using the exported application
   path/version. Write A's readiness marker and model its process exit.
3. Use actual `--stage` with signed B and a local archive, then actual `--launch`.
   The loader consumes A's readiness and selects B as a trial. Prepare B's client
   (`1.0.124`), which changes the shared `dpe-updater/current.json` pointer.
4. Model B exiting after client preparation and before readiness. Do not create
   B's ready marker. Invoke the loader with `DPE_CLIENT_OFFLINE=1` and a fresh
   simulated PID. It selects A and exports A's version, without another trial
   marker. It leaves the client pointer naming B.
5. Validate A's exact old generation with real `validate_host`. Real offline
   authorization returns A's exact package pins and excludes B under the
   application version cap. `ensure_client(..., offline=True)` nevertheless raises:

   ```text
   First launch requires an internet connection; reconnect to repair an unapproved cached client
   ```

6. Switch to ordinary client selection with both download entry points and module
   installation patched to raise if called. It returns the identical A generation,
   rewrites the pointer, and subsequent offline selection succeeds. All three
   forbidden-call mocks record zero calls.

This is a normal trial-failure window: client preparation precedes installation
publication and client readiness, so termination or failure after preparation is
sufficient. It does not require pointer corruption, permission loss, concurrent
launches, edited approval evidence, or cleanup of any retained directory.

## Code references and cause

- [Loader](../../integration/bootstrap/loader.py), lines 341–359, 373–438 and
  490–504: recognize trial readiness/death, fall back among valid application
  generations, and export the selected application's path/version at execution.
- [Readiness](../../integration/updater/application-ready.cjs), lines 1–21:
  confirmation waits for the matching Discord HTTPS window's `did-finish-load`.
- [Preparation](../../integration/updater/check.py), lines 16–33: offline mode
  reaches client selection before installation publication and the ready result.
- [Release cap](../../integration/updater/runtime.py), lines 170–177: correctly
  rejects B's client approval while A is selected. This boundary must remain.
- [Client authorization](../../integration/updater/client.py), lines 140–192:
  always offers exact package pins; rechecks retained approval and returns the
  authorized sources offline. A's pins pass this gate.
- [Client selection](../../integration/updater/client.py), lines 415–457:
  `current_host` considers only `current.json`; B is discarded when its pins no
  longer match an authorized source. The offline branch immediately raises when
  `previous` is absent, without selecting A's other complete cached generation.
- The same file, lines 459–522: ordinary selection computes the authorized
  generation, finds all its modules complete, validates the existing generation
  and publishes its pointer. No repair of A's bytes is necessary.

## Docs, comments and intended limitations

[Client updater design](client-updater.md), lines 15–24 and 79–95, authorizes exact
package pins and retained approved releases. Its requirement to repair older
**unapproved** caches online does not apply to A: this test verifies A is complete
and exactly package-pinned. The code's authorization docstring at `client.py:141`
and legacy-cache warning at line 144 agree; retained bytes alone are not authority,
but A has independent authority. The explicit offline-retention comment at line
328 also weighs against treating all non-current caches as intentionally unusable.

The same design document, lines 59–71, promises fallback after an unconfirmed trial
exits and verified cached code in offline mode. The loader fulfils its part;
client selection defeats that fallback. Lines 26–29 also describe preserving the
package baseline or an approved installation on failed checks.

[Launcher documentation](../linux-launcher.md), lines 101–106, describes offline
mode as skipping update checks and requiring a verified existing installation.
It does warn about rejected older caches, but does not say a sole mutable pointer
is the only usable verified cache. A is neither damaged nor unapproved. The
observed requirement to change mode is therefore a recovery limitation beyond
the documented trust restriction.

[Release design](releases.md), lines 199–213, binds application, client and runtime
through approval and permits module-only updates. The test preserves that binding.
It does not need to permit B's client while running A. The downgrade guard at
`client.py:471–474` is not an objection: B has already been excluded by A's cap,
and the existing ordinary path intentionally recovers A without hitting it.

Manual cache/backup cleanup is explicitly documented at `client-updater.md:153–157`;
no cleanup finding is claimed. Permanent rejection of interrupted application
launches is also excluded. This test uses a genuine failed trial and proves the
loader successfully returns to A; the remaining failure is in client selection.

## Impact, controls and limits

Medium: recoverable startup/preparation blockage for explicit offline selection
after a failed application trial. The error inaccurately describes a first launch
or an unapproved cache and requests an internet repair despite complete authorized
bytes. `failure.cjs:23–26` pauses retries for this error. No approval bypass,
installation destruction, data/mod/settings loss or permanent lockout was shown.

The zero-download recovery control runs while the recent successful approval check
is still throttled (`client.py:194–198`). It does not establish that ordinary mode
never attempts a network check after the cooldown expires. Quickstart alone is
also narrower impact: `startup.cjs:61–94` retries with ordinary selection after its
initial offline attempt; explicit `DPE_CLIENT_OFFLINE=1` remains offline on retry.
This is an updater selection failure, not proof that Discord's hosted web client
can function without a network connection.

Fixture limitations: module payloads and installation are synthetic, and the
compatibility fingerprint is fixed by the existing fixture. This models releases
without compatibility-code changes and keeps A's original fingerprint intact.
The loader's PID/start-time checks and execution boundary are mocked. Approval,
archive/index verification, application state transitions, client authorization,
module integrity records, generation validation and pointer publication are real.
No Electron window, installed client publication, crash or live release was run
by the added reproduction. Initial missing-application diagnostics in its log are
expected before seeding A's local archive, not the claimed failure.

## Overlap with S1 and suggested direction

S1 interrupts the write of `current.json` after a complete pinned generation exists;
`previous` is absent at `client.py:435`. APPROVAL-1 leaves a valid pointer to B, but
authorization for selected A clears `previous` at line 451. Both then hit the exact
same premature offline failure at lines 452–456, while ordinary selection can reuse
the complete authorized cache at lines 459–522. The independently run S1 test passes.

Treat these as **one common defect with two triggers**, not two independent
findings. Neither depends on the excluded permanent trial-rejection or retained
backup/generation cleanup issues. A common correction should attempt validated,
currently authorized cached-generation selection before declaring offline setup
unavailable, with no network or installation work and no weakening of signatures,
package-pin matching, compatibility checks or the application cap. Keep both
reproductions as coverage; no correction was implemented in this review.

## Commands, results and artifacts

From this worktree, all commands use the namespace/offline prefix above:

```sh
uv sync --locked --offline
uv run python -m unittest discover -s tests -p test_repro_approval_reliability.py -v
uv run python -m unittest discover -s tests -p test_verify_approval_rollback.py -v
env PYTHONPATH=tests:/home/he3als/scratch-ai-repo/discord-patched-electron/out/reliability-review-20261030/verify-approval uv run python -m unittest test_repro_state_reliability.RuntimeStateReproduction.test_repro_completed_pinned_client_is_stranded_offline_after_interruption -v
bun run test
bun run lint
```

Each focused reproduction: **1 test, PASS**. Full suite: **343 Python tests and
75 JavaScript tests, PASS**. Lint: **PASS**. These are reproduction assertions of
wrong behavior, not assertions that the defect is fixed.

Artifact directory:
`/home/he3als/scratch-ai-repo/discord-patched-electron/out/reliability-review-20261030/verify-approval/`.
It contains `blind-repro.log`, `verified-rollback.log`, `s1-overlap.log`,
`bun-test.log`, `bun-lint-initial.log`, `bun-lint.log`, `unchanged-code.log`, copies
of both approval tests and the S1 source, and this report. `unchanged-code.log`
records HEAD, empty tracked diff against the baseline and reproduction hashes.
