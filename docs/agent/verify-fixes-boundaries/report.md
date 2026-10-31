# Independent correction review, 31 October 2026

Recommendation: revise the archive link check before accepting this snapshot.
Two reproducible issues share the same new normalization logic: an incomplete
containment fix and a new compatibility regression. No additional qualifying
finding was established in release signing, discovery recovery or advisory policy.
No production fixes were made by this reviewer.

## Baseline and isolation

HEAD was and remains `f90b251caee6b4e7d265c0e450e53a70d97b0aba` in the prepared
`t3-d144e281` checkout. The original dirty repository was not accessed or changed.
The two requested baseline reproductions both passed, asserting the baseline's
incorrect behavior. Their independent policy, realism and impact verdicts were
written to [baseline-verdicts.md](baseline-verdicts.md) before opening the corrective
implementation or investigation notes. Both qualify under the documented policy;
neither proves an unauthenticated remote exploit.

The supplied ZIP SHA-256 matched
`8f1d8e60afa105bf2b67771f0e6c3479aa70d5e21ac0b6c7139035529f03d166`.
All 43 files were overlaid once after the baseline verdicts. Their contents were
subsequently checked against the supplied per-file manifest and remained identical.
Existing investigation notes were not used as evidence. Current release,
client-updater and discovery guides and source comments supplied the policy.

## Finding 1: target aliases still permit relocation escape (P2)

Location: `integration/updater/client.py:281-291`, particularly the lexical
`os.path.normpath` operation at line 282.

Trigger: a synthetic pinned module tar contains, in order:

- `files/value.js`, with ordinary fixture bytes;
- `files/alias -> .`;
- `files/entry.js -> alias/../files/value.js`.

The candidate extractor accepts every entry. The lexical normalization collapses
`alias/..` to nothing, leaving `files/value.js`, which has no `..` component.
Actual filesystem resolution follows alias to the extraction root before applying
`..`; it leaves that root and re-enters through its temporary name, files. The
second containment check therefore also succeeds while staging is named files.
After publication renames the root, entry.js points outside the module.

[repro_alias_relocation.py](repro_alias_relocation.py) demonstrates that entry.js
reads verified fixture bytes before rename, becomes dangling after rename, then
reads an unrelated synthetic sibling when that sibling is created. Its successful
assertions reproduce the defect; they are not a passing safety regression test.
The output is in [alias-relocation.log](alias-relocation.log).

This is the same realistic production boundary as the supplied baseline case:
install_module extracts into files and publishes under a different module cache
name. Such an unusual link must arrive in a pinned archive; the test does not
show that an arbitrary network attacker can inject it. Impact is broken module
content, or bytes from outside the verified module if the target exists. It
violates the documented rejection of external links after publication. This is
an incomplete correction, not a newly introduced escape.

## Finding 2: valid target aliases are newly rejected (P2)

Location: `integration/updater/client.py:282-288`.

Trigger: an otherwise ordinary synthetic module has value.js at its root,
a directory deep/nested, `alias -> deep/nested`, and
`entry.js -> alias/../../value.js`.

Actual resolution follows alias down two levels and returns to value.js inside
the module. The candidate instead lexically reduces the target to ../value.js
and rejects the archive. Baseline extraction accepts the same archive. Reading
the real link before and after relocation confirms containment and identical
bytes in both locations. A pinned upstream module using this valid link layout
would now fail preparation rather than being installed. No claim is made that a
current Discord distribution uses this layout.

[repro_valid_target_alias.py](repro_valid_target_alias.py) is the fixture;
[valid-target-alias.log](valid-target-alias.log) records candidate rejection and
filesystem controls, while [valid-target-alias-baseline.log](valid-target-alias-baseline.log)
records acceptance by the baseline module loaded directly from git show. Loading
that baseline module in memory did not revert or rewrite any candidate source.

Both findings arise because resolving the link's parent is insufficient: the
link target itself can contain directory symlinks. The correction needs to check
the symlink graph with module-root semantics, including target aliases and
relocation, while retaining valid internal links. Add both fixtures to the
regressions; fixing only the lexical depth check in either direction would leave
the other case unresolved.

## Other correction review

`scripts/release_signing.py:29-60` now verifies signatures against the pinned key
and binds release.json and exactly its asset set to the signed checksum list.
The supplied end-to-end mocked approval regression now rejects the modified
manifest. Non-Arch replacement bytes, malformed/duplicate/missing checksum rows,
wrong hashes and incomplete signing artifacts are covered. Entirely unsigned
legacy releases remain allowed only through required=False; partially signed
releases still fail verification.

At lines 99-155, signing checks existing artifacts, stages missing signatures,
verifies them before publication, uses exclusive hard links to preserve existing
files, and verifies the final result. Additional controls exercised all eight
subsets of existing signature/key artifacts, exact byte preservation, a wrong
existing key, and a publication collision containing a corrupt signature. The
collision preserved the conflicting bytes and failed final verification. These
controls support both retry recovery and strict final verification; they do not
model hostile concurrent mutation of arbitrary release payloads.

`scripts/discover.py:201-210` now seeds channels lacking a release record after
initial feed failure. The full mocked main-path regression proves that persisted
client state no longer prevents recovery and that activated_at stays fixed.
Withdrawn, moved, replaced, invalid and missing-source history does not cause
historical backfill. Additional controls show that rejected seed history does
not prevent discovering a genuinely new post-activation release. A still-empty
feed fails without mutating the caller's state and can later recover.

`scripts/update_policy.py:20-23,49-64` treats null/non-string patched versions and
ranges as unusable advisory evidence, retaining client work and the cooldown.
Additional mixed-advisory controls show that a malformed entry does not hide a
later valid high/critical fix or an explicit maintainer override. Existing tests
retain affected-current, fixed-candidate, severity, withdrawal and major-version
restrictions. Arbitrary malformed advisory schemas beyond these fields were not
exhaustively tested.

## Validation and reproduction

Every test and tool invocation using Python ran with uv run, with dependencies
prepared using cached `uv sync --locked --offline`. Test commands used this prefix:

```sh
unshare --user --map-current-user --net env UV_OFFLINE=1
```

Append `uv run python` and the desired fixture path to reproduce the standalone
fixtures from the checkout root. The six additional controls run with:

```sh
unshare --user --map-current-user --net env UV_OFFLINE=1 \
  uv run python docs/agent/verify-fixes-boundaries/independent_controls.py
```

Results, with full logs beside this report:

- Baseline claims: 2/2 reproduced (`baseline-release.log`, `baseline-archive.log`).
- Affected suites: release signing 8, client updater 17, discovery 15 and policy
  13; all 53 passed. These are included again in the full-suite count below.
- Additional independent controls: 6/6 passed (`independent-controls.log`), with
  eight signing-artifact subsets and four rejected-history statuses as subcases.
- Archive probes: the two findings reproduced on the candidate; the valid-target
  fixture also accepted by the baseline and remained internal after rename.
- `bun run test`: 368 Python tests and 86 JavaScript tests passed; no skips or
  failures (`full-test.log`). Total distinct passing suite/control methods: 460.
- `bun run lint`: passed (`lint.log`). An initial lint invocation caught only
  E402 imports in the new standalone review controls; explicit import-path
  annotations corrected those review-file issues before the successful run.

The required lint includes Ruff, oxlint, oxfmt, shell/C style checks and actionlint.
Targeted documentation additions introduce no changed external links; their
instructions were compared with code and tests.

## Coverage limits and handoff

This is an offline review of the specified signing, extraction, discovery and
policy boundaries. Other files in the 43-file snapshot received full-suite
coverage, not an independent implementation audit. No external service, real
installation, account, call, message, profile, signing credential or physical
device was used. All keys were disposable and all archive contents synthetic.
No runtime compilation, paid CI, package/container qualification or live Electron
launch was performed. CI check-selection documentation calls for Arch packaging
for updater changes; that broader integration coverage remains outside this
explicit offline/synthetic assignment. Passing unit tests do not establish that
a live call, real package installation or current upstream archive works.

The excluded interrupted-update rejection and generation/backup collection
issues were not raised. The reviewer added only this report, fixtures and logs,
plus the authorized dependency setup. Report and evidence are copied to the
parent artifact directory `out/project-reliability/verify-fixes-boundaries/`.
