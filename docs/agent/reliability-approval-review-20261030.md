# Offline approval review, 30 October 2026

Baseline and unchanged production HEAD: `f90b251caee6b4e7d265c0e450e53a70d97b0aba`.
Worktree: `/home/he3als/.t3/worktrees/discord-patched-electron/t3-1d29112e`.
Area: approval. One medium-severity candidate; independent verification pending.
No production fixes, upstream/patch edits, external Git operations or live services.

## Candidate APPROVAL-1: offline rollback ignores a verified baseline client

**Severity: medium.** A failed application trial can leave a newer client selected
in the profile-wide pointer. After the loader returns to the older application,
explicit offline startup fails even though that application's complete, authorized
package-pinned client generation remains on disk. No authorization bypass, data
loss or permanent rejection of an application is claimed. Online-mode selection
recovers without downloading anything in the reproduction.

### Observed behavior and reproduction

[test_repro_approval_reliability.py](../../tests/test_repro_approval_reliability.py),
`ApprovalRollbackReproduction.test_repro_offline_rollback_ignores_intact_package_pinned_client`,
is labeled **REPRODUCTION** and passes on the unchanged baseline by asserting the
wrong result, not the proposed fix.

1. The existing `RuntimeUpdates` fixture supplies a disposable Ed25519 key, a real
   signed release, mocked release downloads and synthetic module installation.
2. Application A (`20261024.120000`) prepares its exact package-pinned client
   `1.0.123`. The newer signed release is correctly blocked by the application cap.
3. Application B (`20261025.120000`) has a distinct application directory, updated
   client pins and its own runtime policy/key file. It prepares approved client
   `1.0.124` in the same disposable profile. The current client pointer changes.
4. Model the documented rollback by restoring A's application directory and
   `DPE_APPLICATION_VERSION`. This is the fixture boundary; no application is run.
5. The older generation passes `validate_host`. `approved_sources` returns A's
   package pins, exactly matching that intact older generation. It correctly
   refuses B's approval because it is newer than the selected application.
6. `ensure_client(..., offline=True)` nevertheless raises
   `First launch requires an internet connection; reconnect to repair an unapproved cached client`.
   It leaves the newer pointer selected.
7. As a control, online-mode `ensure_client` selects the original older generation
   with all network access forbidden and no additional synthetic module installs.
   Subsequent offline selection succeeds.

Expected: reject B's incompatible release while selecting the already verified,
authorized A generation without network access. Observed: reject startup before
considering A's intact generation.

The supported trigger is a real failure after the new application's client
preparation but before its Discord ready confirmation, followed by an offline
restart. A fresh package or portable application using the same profile and older
exact pins also exposes the selection conflict. This does not require changing
approval metadata, an attacker, revoked signatures, or corrupt module files.
It is distinct from the excluded permanent rejection of interrupted launches:
this candidate assumes the application rollback is appropriate and has succeeded.

### Original reasoning and code references

Keep this section separate from the blind verification brief.
All line references below refer to the baseline.

- `integration/updater/runtime.py:170-177` correctly excludes approvals newer than
  the selected application.
- `integration/updater/client.py:146-151` still supplies the old application's
  authoritative package pins; `178-192` drops the now-ineligible newer proofs.
- **`integration/updater/client.py:446-457`** clears `previous` because its pins no
  longer match, then immediately raises for offline mode. It does not look up the
  older authorized generation.
- `integration/updater/client.py:459-468` computes the authorized generation only
  after that offline return/error. The later online path can reuse the exact
  complete baseline cache, as the control demonstrates.
- `integration/bootstrap/loader.py:341-364,374-386` handles application trial
  fallback. `490-497` exports the selected application's version.
- `integration/updater/check.py:18-26` checks the selected application's modules
  before publishing; it propagates this client-selection failure to startup.

Suggested correction: under the existing update lock, when the current client
cannot be authorized, resolve and validate a retained generation for the current
application's exact pins (and compatibility identity) before returning an offline
error. Keep the release-version ceiling and all file/signature validation. This
must not download or execute new code during offline recovery. Add a regression
that expects the older intact generation instead of the reproduced exception.

### Intended-limitation check

- [Client updater](client-updater.md), lines 59-70, promises application trial
  rollback and verified cached code in offline mode. Lines 91-95 require matching
  package pins or retained approval. This reproduction has matching package pins.
- Lines 26-29 describe retaining the baseline or previously approved installation
  on failures. The reproduction does not ask to retain the newer incompatible
  release or accept an unapproved legacy cache.
- `client.py:140-145` documents exact package pins and the offline approval gate;
  `176-177` discusses retaining approved clients on failure. Neither states that
  an intact package-pinned generation is unusable once a newer pointer exists.
- `docs/agent/updater-history.md:55-65,264-267` documents approval and rejecting
  downgrades while retaining usable installations. That is not a requirement to
  leave the selected client newer than the application: online mode already
  restores the baseline in this same scenario.
- Explicit offline mode is documented in `docs/linux-launcher.md:101`. Quickstart
  can retry online (`integration/updater/startup.cjs:90-93`), reducing impact for
  Quickstart users; this candidate concerns explicit offline selection.

## Coverage and limitations

Additional [approval controls](../../tests/repro_approval_controls.py) pass for:

- mismatched approval schema, tag, version, manifest hash and empty approver;
- modified manifest bytes with the original signature/checksums;
- draft/prerelease metadata and inconsistent signed Electron version fields;
- retained offline approval after a simulated remote supersession;
- a real historical signature made with a disposable one-day key at a fixed past
  date, verified after key expiry (accepted by GPGV; not reported as a defect);
- release timestamp ordering across midnight and numeric alpha/beta revisions;
- changed approval evidence after updater validation but before loader staging,
  rejected by the real loader with no payload execution;
- a failed retained-evidence copy followed by successful repair. Publication is
  atomic because the proof is assembled in a temporary directory before rename.

The key-expiry control does not establish signature-expiry or revocation behavior;
manual approval records have no expiry field/policy. Retained offline approvals
are explicitly supported by `runtime.approved_release`'s docstring. No issue is
claimed merely because an old approval remains usable.

The failing test reuses existing fixtures and verifies real signatures. Module
installation is mocked using the existing `RuntimeUpdates.modules` boundary;
compatibility identity is fixed by that fixture. Application rollback is modeled
by changing the selected application root/version, not by launching or crashing
Discord. No real downloaded application/runtime/module code is executed by these
review tests. The required full suite uses its existing synthetic/native fixtures.
No physical audio/camera/desktop capture, account, production profile, credentials,
real signing keys, releases, CI or runtime compilation were used.

## Evidence and commands

Run from the worktree root. All test/lint commands run in a network namespace:

```sh
unshare --user --map-current-user --net env UV_OFFLINE=1 uv sync --locked --offline
unshare --user --map-current-user --net env UV_OFFLINE=1 uv run python -m unittest discover -s tests -p test_repro_approval_reliability.py -v
unshare --user --map-current-user --net env UV_OFFLINE=1 uv run python -m unittest discover -s tests -p repro_approval_controls.py -v
unshare --user --map-current-user --net env UV_OFFLINE=1 bun run test
unshare --user --map-current-user --net env UV_OFFLINE=1 bun run lint
```

Local logs: `out/reliability-approval/`. Shared evidence copies, blind brief,
commands and final logs:
`/home/he3als/scratch-ai-repo/discord-patched-electron/out/reliability-review-20261030/approval/`.
Dependencies were synced offline; the original checkout's existing `node_modules`
was linked for read-only use, with no installation or writes to its contents.

Validation results are recorded in the shared `results.txt` and the logs.
