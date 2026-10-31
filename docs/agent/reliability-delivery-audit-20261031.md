# Reliability delivery audit, 31 October 2026

The offline correction pass is complete. All four original confirmed findings
have regression fixes. Three independent fix reviews completed; the archive and
launcher regressions they identified were corrected and their cases rerun locally.
The final suite passes **375 Python and 90 JavaScript tests**, up from the baseline
341 and 75. Required lint and the additional controls are recorded below.

## Scope and provenance

The user authorized fixes after the original offline review and expanded the work
across the project. The [original report](reliability-review-20261030.md) remains a
historical baseline report. The [first correction set](reliability-fixes-20261031.md)
and [project-wide follow-up](project-reliability-20261031.md) describe the changes.
The original review ledger and wrong-behavior reproduction archive are unchanged.

All corrections are local to branch `review/offline-reliability-20261030`, based on
cached commit `f90b251caee6b4e7d265c0e450e53a70d97b0aba`. The original dirty
checkout's working files have not been edited. Pinned upstream files, patches,
OpenAsar inputs, configuration and dependency locks are unchanged.

Three independent reviewers received the fixed snapshot
`out/project-reliability/fixes-review-snapshot.zip` with SHA-256
`8f1d8e60afa105bf2b67771f0e6c3479aa70d5e21ac0b6c7139035529f03d166`.
Its manifest records hashes for all 43 supplied files. The final implementation
differs from that snapshot only in the archive and launcher corrections described
below; tests and documentation also include the resulting coverage. The approval/archive reviewer received separate
baseline reproduction tests and claims, to assess before reading the corrections.

## Independent review and disposition

Reports and original reviewer fixtures are preserved under
`out/project-reliability/verify-fixes-state-audio/`, `verify-fixes-boundaries/` and
`verify-fixes-lifecycle/`. These are completed reviews of the immutable snapshot,
not claims that the reviewers examined the final revision.

- State/audio: no actionable regressions. Five independent Python controls and
  four JavaScript controls were incorporated unchanged into the normal suite.
- Approval/archive: the reviewer first reproduced both baseline claims and checked
  current policy without reading the corrections. Signature binding, signing retry,
  discovery recovery and advisory controls passed. Two P2 archive cases required
  correction: target aliases could still escape after relocation, and valid target
  aliases could be rejected. Component-by-component resolution now follows aliases
  before `..`, checks write parents, and validates the complete link graph before
  publication. Both link orders, cycles and attempted redirected writes are covered.
- Launcher/lifecycle: flags recovery and helper controls passed. A P3 regression
  for bundled paths containing `;` was corrected by retaining direct-path loading
  where possible. The reviewer's unchanged normal/space/semicolon matrix passes.

The final small corrections were made by the coordinator. Their passing regression
tests and unchanged independent fixtures are the final verification; no second
independent code review of those corrections is claimed. Original reviewer logs
remain intact beside the new `review-corrections.log` and
`camera-independent-recheck.log`.

## Fix order and user impact

The following order follows the requested impact categories. Original severity
ratings remain in the historical review. The two additional baseline defects were
independently reproduced with documented policy and impact checks; medium is the
coordinator's delivery priority for those two, not a claimed live exploit rating.

| Order | Correction                                                             | Evidence and limits                                                                                                                                                                          |
| ----- | ---------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1     | Bind release approval to signed manifest and asset checksums           | Modified signed-release metadata previously passed mocked approval. Runtime update verification already bound its manifest; installed execution of unapproved code was not demonstrated.     |
| 2     | Preserve selected audio across parent-only PID changes                 | AUDIO-2, medium; PID/start time and UID remain enforced. Native source exceptions also clear intent defensively; AUDIO-1 remains disputed as a user-facing leak.                             |
| 3     | Repair unusable cached runtimes and recover authorized offline clients | STATE-2 and STATE-1/APPROVAL-1, medium; no download is needed when the exact intact authorized client generation survives.                                                                   |
| 4     | Keep archive targets inside the published module                       | A pinned archive with a staging-name-dependent link could become dangling or read an unrelated sibling after publication. No current upstream archive is claimed to contain it.              |
| 5     | Preserve repairable settings and restore interrupted preparation       | Invalid-encoding flags keep exact backup bytes; interrupted signing can resume; initial discovery failure can recover. Approval requirements and original activation cutoff remain enforced. |
| 6     | Respect cancellation and terminate helper descendants                  | Output is framed per record, ignored after cancellation, and owned helper groups are cleaned on success, failure and cancellation.                                                           |
| 7     | Retain surviving-instance tracking and camera helper relocation        | STATE-3 remains low severity and experimental; no crash or data loss was proven. Normal, space and semicolon launcher paths pass synthetic checks.                                           |

Other input hardening keeps malformed advisory evidence from granting a cooldown
bypass or aborting planning. These additional local corrections are distinguished
from the original independently confirmed findings in `fixes-ledger.tsv`.

### Release approval binding reproduction

At baseline `scripts/release_signing.py:28`, verification authenticated the checksum
text and Arch package but did not bind that text to the current `release.json` or
other assets. A stale signed checksum list paired with a changed manifest and
matching mocked upload digests passed `releases.approve(check_only=True)`. A partial
release copy or manifest edit before publication is a realistic trigger; changing
downloaded bytes alone would still fail the separate upload-digest check.

The clearly labelled wrong-behavior test is
`out/project-reliability/repro_release_binding.py`, with adjacent
`signing_fixture.py`. It passes on baseline by asserting the incorrect acceptance.
The independent baseline run and policy check are in
`verify-fixes-boundaries/baseline-release.log` and `baseline-verdicts.md`.
The release guide requires immutable signed manifests and pinned-key verification;
the unsigned-legacy exception does not permit stale checksums on signed releases.

`scripts/release_signing.py:29` now checks exactly one entry per asset plus the
manifest, binds their hashes to the signed list, and rejects ambiguous entries.
`tests/test_release_signing.py` uses disposable keys and mocked release operations
to assert rejection before any approval mutation. Signing retry uses the same
validation and preserves valid existing signatures. Nothing was approved remotely.

### Archive relocation reproduction

At baseline `integration/updater/client.py:274`, final-path resolution accepted
`files/entry.js -> ../files/value.js` while staging was named `files`. Publication
renamed the root, so the link became dangling or pointed outside the verified
module. The labelled baseline reproduction is
`out/project-reliability/repro_archive_relocation.py`; its independent run and
limitation check are in `verify-fixes-boundaries/baseline-archive.log` and
`baseline-verdicts.md`. The existing internal-link policy contains no publication
exception. The production correction starts at `client.py:248`; normal executable
links and valid directory aliases remain supported. The added normal-suite tests
exercise both review findings and preservation after relocation.

## Evidence checklist

| Requirement                                                           | Current evidence                                                                                                                        | Status                                                            |
| --------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| Repair the four original confirmed findings                           | Regression tests and controls for audio identity, runtime execute permission, authorized offline cache recovery and surviving instances | Pass; independent fix review found no actionable regression       |
| Preserve approval requirements                                        | Existing signature/approval unit tests and eight standalone approval controls; additional disposable-GPG binding tests                  | Pass; independent tooling controls pass                           |
| Preserve installation, database, mods and settings on interruption    | 163 installation publication/recovery fault cases, including both supported mod loaders                                                 | Pass on current fixes                                             |
| Keep source selection and cleanup bounded to the selected application | Mocked audio graph/controller tests and UID/PID-reuse controls                                                                          | Pass locally; no real audio capture or call claim                 |
| Preserve user settings during repair                                  | Flags recovery tests verify original bytes, backups, no preview writes and reload after save                                            | Pass; independent checks include file mode and revision conflicts |
| Bound helpers and respect cancellation                                | Real synthetic process-group tests and deterministic completion/cancellation interleavings                                              | Pass; independent control preserves an unrelated process session  |
| Keep archive links inside their published module                      | Baseline relocation reproduction and final internal-link/alias controls                                                                 | Pass locally after both review findings were addressed            |
| Run required checks                                                   | 375 Python tests, 90 JavaScript tests, lint and `git diff --check`                                                                      | Pass                                                              |
| Deliver a local report without external publication                   | Repository reports, logs, preserved baseline archive and supplemental fix ledger                                                        | Complete locally; changes remain uncommitted                      |

Current logs are under `out/project-reliability/`: `test.log`, `lint.log`,
`final-installation-matrix.log`, `final-approval-controls.log`,
`delivery-integrity.json` and `fixes-ledger.tsv`. Supplemental ledger statuses
distinguish original confirmed findings, disputed cases and additional corrections.
Standalone final controls also pass: six signing/discovery/advisory cases, three
flags/process cases, seven runner cases and the three-path camera matrix. Counts
overlap with normal-suite coverage and should not be added together.

The local delivery archive is `out/reliability-delivery-20261031.zip`; its
`DELIVERY-README.txt` explains the patch, snapshots, reports and historical evidence.
The patch targets the recorded baseline and includes new tests and documentation.
It has been checked against the shared clean baseline without changing that checkout.

## Integration limits

The changed-path selector was evaluated locally without triggering CI. Its result
is retained in `prospective-ci-scope.json`. The combined change selects every
integration category: the two standalone review-control filenames are unclassified
and therefore take the selector's conservative full-check path. No workflow,
compiler-cache job, source assembly, container or live desktop qualification was run.
This is a prospective check selection, not a claim that those integrations passed.

The installed-loader change needs a package upgrade when eventually deployed.
The remaining application changes can use the existing signed application path;
release-tooling changes affect future local/CI operations. Nothing was deployed,
pushed, approved, released or published externally.

All tests use network-isolated namespaces, cached dependencies, fresh disposable
files and test keys. Tool versions are Bun 1.4.4, uv 0.9.16 and Node 26.12.1;
contributors recommend Node 24, while the declared engine permits this Node version.
Tiny native fixture/helper builds are part of the existing unit suite; no Electron
or Chromium compilation was started.

Physical power loss/torn writes, concurrent stock-updater activity, real desktop
timing, PCM/audio isolation in actual calls and deployed installations remain
outside the offline evidence. Permanent rejection of interrupted update launches
and uncollected generations/backups remain the user's two explicit exclusions.

The last 20 relevant code commits were listed in `relevant-history.txt` and recent
bootstrap-delay, launcher integration, readiness, recovery and scheduling changes
were inspected. This is targeted historical context, not a complete 20-commit audit
or a mutation-testing campaign.
