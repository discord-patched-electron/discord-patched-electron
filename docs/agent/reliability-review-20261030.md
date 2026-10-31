# Offline reliability and safety review

Historical baseline report. The subsequent [local fixes](reliability-fixes-20261031.md)
convert the linked working-tree reproductions to regression assertions. The
original wrong-behavior tests remain preserved in the review evidence archive
under `out/reliability-review-20261030/`.

Review dates: 30–31 October 2026. **Four independently confirmed findings:
three medium and one low.** No incorrect acceptance of an unapproved update,
cross-application audio capture, user-data loss or mod/settings loss was confirmed.
No production fixes were implemented. This report and its evidence are local;
nothing was published as a Page or sent to an external service.

The baseline is the locally cached `origin/main`,
`f90b251caee6b4e7d265c0e450e53a70d97b0aba`. No fetch was performed. The local
`main` branch and original dirty feature checkout were older; neither was used
as the reviewed baseline or modified. All references below refer to this commit.
Four initial reviewers used separate worktrees; three new reviewers independently
verified candidates in clean worktrees from their claims and tests, without the
originating analysis. Six candidate reports became four findings after one
duplicate was consolidated and one candidate disputed.

## Confirmed findings, ranked by requested user impact

| Order | ID                   | Severity | Confirmed effect                                                                                                                             |
| ----- | -------------------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| 1     | AUDIO-2              | Medium   | Reparenting a surviving selected application stops Wayland share audio until reconfiguration.                                                |
| 2     | STATE-2              | Medium   | Cached Electron without execute permission is selected as usable, bypassing startup repair.                                                  |
| 3     | STATE-1 / APPROVAL-1 | Medium   | Explicit offline preparation ignores a complete authorized client cache after interrupted publication or application rollback.               |
| 4     | STATE-3              | Low      | Experimental multiple-instance mode loses survivor tracking and permits a newer application selection while an older instance remains alive. |

The last finding establishes an activation-order gap, not file replacement,
startup failure, data loss or approval bypass. It is last because its demonstrated
user impact is narrower than the requested failure categories.

### AUDIO-2: ancestry changes invalidate a live audio selection

**Trigger.** On niri with an exact active window/cast relationship, a selected
application survives the exit of its shell or launcher. Its parent PID changes
to init or a subreaper; its PID, start time, UID, selected window and capture
relationship remain valid. The application must survive its parent; ordinary
application exit should correctly silence capture.

**Observed behavior.** The current audio route closes, reports unavailable, and
stays unavailable on subsequent ticks. A new controller generation restores the
same application's streams. Audio off/on or reselection is sufficient recovery.
No cross-application capture or PCM leakage was demonstrated.

**Cause and references.** `integration/audio/pipewire_audio.py:37–49` returns
`(pid, start_time, ppid)` after UID validation. The controller compares the entire
tuple at `628–640` and mistakes changed ancestry for a replaced process, retaining
the old key. Its descendant check at `52–62` already uses stable PID/start-time
identity separately from ancestry. The unavailable reply silences the native
transport through `integration/audio/wayland-audio.cjs:113–125`.

**Intended-limitation check.** The comment at `pipewire_audio.py:634–635` guards
against vanished/reused processes, not living reparented ones.
`docs/agent/wayland-audio.md:25–29,47–51` promises application/descendant selection
using UID and start time. Its unrelated limitations at `43–45` do not exclude this
niri case. Independent controls using the production process-stat parser confirm
that changed start time and UID still need to be rejected.

**Suggested correction.** Compare stable PID/start-time identity for replacement
detection, retaining UID checks and current parent information for descendant
traversal. Keep the real PID-reuse guard.

**Reproduction and independent evidence.**
[Passing wrong-behavior reproduction](../../tests/test_repro_audio_reparent.py),
[independent identity controls](../../tests/test_verify_audio_reparent.py), and
[full independent assessment](reliability-review-audio-independent-20261030.md#a2-mutable-ancestry-is-mistaken-for-replacement-identity).
The reproduction uses the existing exact graph/cast fixture and real route-sync
logic, with synthetic playback/process graphs and mocked process boundaries.

```sh
unshare --user --map-current-user --net env UV_OFFLINE=1 uv run python -m unittest discover -s tests -p test_repro_audio_reparent.py -v
```

### STATE-2: non-executable cached Electron bypasses repair

**Trigger.** A profile restore or copy loses the cached Electron file's Unix
execute bits without changing its bytes. The updater is not claimed to cause
the permission loss. The fixture changes the seeded executable to mode `0600`.

**Observed behavior.** Validation accepts the cache, and offline, bootstrap and
ordinary selection return its non-executable path. Actual `bootstrap.prepare`
returns without starting preparation. Independent controls show that retry never
probes or reinstalls it, even when a known-good local archive is supplied offline.
Restoring execute permission alone restores executable usability. The tests stop
at mocked execution; they do not launch real Electron.

**Cause and references.** `integration/updater/runtime.py:290–305` verifies
presence, resources and hashes without executable permission. `317–322`,
`473–476` and `503–504` return the accepted generation and bypass probe/repair at
`370–378`. `integration/updater/bootstrap.py:18–23` returns it immediately;
`integration/launcher/discord-launcher.sh:62–73` uses it to start the UI. Startup
obstruction follows from that call site and the non-executable mode, rather than
an observed live crash.

**Intended-limitation check.** `docs/agent/client-updater.md:110–119` requires an
intact offline cache and describes damaged-cache repair. Neither the reuse comment
at `runtime.py:327–328` nor the cached-start optimization at `bootstrap.py:24–25`
defines a non-executable runtime as usable. This is a recoverable metadata-damage
case, not a promise of arbitrary restore compatibility.

**Suggested correction.** Check executable usability during cached validation;
route failure through repair or a clear offline diagnostic. Any permission
restoration must follow integrity verification.

**Evidence.** Run the `test_repro_nonexecutable_runtime_is_selected_without_repair`
method in [state reproductions](../../tests/test_repro_state_reliability.py).
[Independent controls](../../tests/test_verify_state_controls.py) explicitly fail
if a retry probe occurs. See the [independent report](reliability-state-verification-20261030.md#s2-lost-runtime-execute-permission-is-accepted).

### STATE-1 / APPROVAL-1: offline selection ignores another usable cache

**Triggers.** A process terminates after a complete package-pinned client generation
is published but before `current.json`; or an approved application trial B prepares
its client, exits before ready confirmation, and the loader rolls back to A while
the shared client pointer still names B. A's intact, package-pinned cache remains.
Explicit offline selection rejects it as requiring internet repair. Normal-mode
selection can reuse the identical generation with downloads and installation
forbidden. Quickstart normally falls back to ordinary preparation; the finding
concerns explicit offline mode, not every disconnected launch.

**References and impact.** `integration/updater/client.py:415–457` considers only
the current pointer and raises before searching the other authorized cached
generation. Ordinary selection at `459–522` can reuse it. `runtime.py:170–177`
correctly rejects B under A's application cap; that protection must remain.
This causes recoverable startup/preparation blockage and a misleading first-launch
or unapproved-cache error, not mandatory redownload, lost data or permanent update
rejection. The rollback test independently exercises real loader staging, trial
selection and fallback, stopping at mocked payload execution.

**Intent and correction.** `docs/agent/client-updater.md:59–71,79–95` supports
fallback and authorized offline caches. The unapproved-cache restriction and
`client.py:141,144` do not explain rejecting A's exact verified package pins.
Select a fully validated, compatible, currently authorized cached generation
before offline failure, without weakening signatures, pin matching or release caps.

**Evidence.** [Interrupted-publication reproduction](../../tests/test_repro_state_reliability.py),
[rollback reproduction](../../tests/test_repro_approval_reliability.py),
[independent real-loader rollback](../../tests/test_verify_approval_rollback.py),
and [independent duplicate/intent assessment](approval-1-independent-verification-20261031.md).
Keep both triggers as coverage of **one** common defect.

### STATE-3: multiple-instance survivor tracking is incomplete

**Trigger and effect.** Launch two independently surviving instances with the
exposed `--multi-instance` option, stage approved application code, then exit only
the first instance. A third launch selects the pending generation while the second
old instance remains alive. The loader keeps one running record and returns from
second launch without registering it (`integration/bootstrap/loader.py:360–371,
435–440`). Readiness and execution boundaries are at `345–349,503–504`.

**Intent and limits.** The forwarding assumption in `loader.py:368–369` fits normal
single-instance operation. `startup.cjs:44–49` also supports independently running
instances, while the pinned settings UI labels Multi Instance experimental
(`integration/updater/openasar/config.html:241–243,256`). Current updater docs
`59–66` state no exception to retaining the running generation. The independently
verified model confirms mixed-generation selection; old immutable files remain
valid. No crash, failed launch, data loss or completed readiness of the new trial
was demonstrated. Severity is low, downgraded from the initial medium claim.

**Correction and evidence.** Track every independently live instance with PID/start
time, or define and enforce a narrower update policy for this opt-in mode.
[Reproduction](../../tests/test_repro_state_reliability.py),
[real-stage control](../../tests/test_verify_state_controls.py),
[independent assessment](reliability-state-verification-20261030.md#s3-a-surviving-second-instance-is-absent-from-running-state).

## Exclusions and review coverage

- **Disputed AUDIO-1 is excluded.** X11 setters retain a new PID after an injected
  native exception, but realistic persistent rejection was not established.
  Source-ended and disappeared-window controls clear capture. No high-severity
  leakage claim is supported. The [independent objections](reliability-review-audio-independent-20261030.md#a1-rejection-commits-audio-selection-before-native-acceptance)
  and [conditional reproduction](../../tests/audio/repro_x11_rejected_source.test.cjs)
  are retained for audit, not counted as a confirmed finding.
- **Installation recovery:** [163 controlled fault cases](installation-reliability-review-20261030.md)
  passed across publication, database commit, repeated recovery, stock takeover
  and legacy migration. They check working files, database records, both supported
  mods and external settings/payload preservation. No candidate qualified.
- **Approval consistency:** [eight standalone controls](../../tests/repro_approval_controls.py)
  cover signature/approval mismatches, ordering, supersession/expiry and changes
  between validation and staging. No approval-bypass finding qualified. Retained
  approved offline evidence and exact package pins are documented exceptions;
  no unsupported freshness policy was invented.
- **State and audio:** existing fixtures and added deterministic controls cover
  staged/ready transitions, handoff, offline operation, package/runtime reuse,
  process identity, source changes, exclusion and cleanup. Tests establish control
  decisions and transaction state, not live call or desktop behavior.
- Permanent rejection of interrupted update launches and uncollected generation
  or backup directories were excluded as requested, not counted as new findings.

Assessing state then approval yields two consecutive areas with **no independently
confirmed finding above medium**, so optional expansion stopped. Audio and recovery
were already running and finished. No optional archive/launcher/Electron/release
review, 20-commit regression sweep or mutation campaign was started. Existing
fixture checks of those components are not represented as separate focused audits.

Physical power loss, torn writes, real audio timing/PCM, account-connected Discord,
actual restored profiles, concurrent stock-updater writes and live multi-instance
behavior remain untested. The disputed X11 trigger remains unresolved and is not
needed to accept any confirmed finding. Review budgets were ceilings of roughly
45–60 minutes per initial reviewer; completed evidence permitted earlier returns.

## Suggested fix order and validation

1. AUDIO-2: restore stable identity comparison, preserving UID/PID-reuse exclusion.
2. STATE-2: reject or repair a verified runtime that cannot be executed.
3. STATE-1: repair offline cached-generation selection; retain both interruption
   and rollback reproductions and all approval gates.
4. STATE-3: settle multiple-instance update policy, then implement its tracking.

Reproduction tests deliberately **pass by asserting wrong baseline behavior**.
They are review evidence, not regression tests asserting a fix. When implementing
corrections, change those assertions to the intended behavior. No correction is
included in this delivery.

Baseline `bun run test`: **341 Python and 75 JavaScript tests passed**; compared
with the supplied reference, Python is unchanged and JavaScript has 19 additional
tests. Baseline lint passed. An initial namespace mapped the user to root and
made `makepkg` refuse to run; rerunning with `--map-current-user` resolved that
harness error without source changes. Bun 1.4.4, uv 0.9.16 and Node 26.12.1 were
available; contributor docs recommend Node 24, while the declared engine permits
this version. Dependencies came only from existing local caches.

From this review checkout, run the complete collected evidence with:

```sh
unshare --user --map-current-user --net env UV_OFFLINE=1 bun run test
unshare --user --map-current-user --net env UV_OFFLINE=1 uv run python tests/repro_installation_boundary_matrix.py
unshare --user --map-current-user --net env UV_OFFLINE=1 uv run python -m unittest discover -s tests -p repro_approval_controls.py -v
unshare --user --map-current-user --net env UV_OFFLINE=1 bun run lint
```

The [findings ledger](../../out/reliability-review-20261030/findings-ledger.tsv),
[shared invariant checklist](../../out/reliability-review-20261030/checklist.md),
baseline logs, original evidence and independent logs are retained under
`out/reliability-review-20261030/`. The final validation results and shareable
archive are recorded there. All production, existing test, configuration, lock,
pinned upstream and patch files remain unchanged; only review documents and
new fixture tests are added. No network access, production services, real accounts,
personal data, real signing keys, devices, live releases, paid CI or runtime builds
were used. No push or external publication was performed.

Final collected validation: **351 Python tests and 80 JavaScript tests passed**,
plus the separate **163-case installation matrix** (three methods) and **eight
approval controls**. Final lint passed. See [test output](../../out/reliability-review-20261030/final-test.log),
[matrix output](../../out/reliability-review-20261030/final-installation-matrix.log),
[approval controls](../../out/reliability-review-20261030/final-approval-controls.log)
and [lint output](../../out/reliability-review-20261030/final-lint.log).
