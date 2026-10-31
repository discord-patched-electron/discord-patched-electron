# Offline installation reliability review, 30 October 2026

Result: **no candidates meet the finding bar** in the bounded checks below.
Production code and pinned upstream files were unchanged. This is a focused
installation review, not a claim that all updater failure modes are safe.

Baseline HEAD: `f90b251caee6b4e7d265c0e450e53a70d97b0aba`.
The prepared review worktree is
`/home/he3als/.t3/worktrees/discord-patched-electron/t3-14b05312`.
The initial worktree was clean. CONTRIBUTING.md, the agent index and the shared
review checklist were read before reviewing implementation code.

## Observations and independently runnable coverage

[The standalone matrix](../../tests/repro_installation_boundary_matrix.py) reuses
`tests/test_installation.py:NativeInstallation` and `tests/mod_fixture.py`.
It is explicitly labeled coverage, not REPRODUCTION: no test asserts an observed
defect. There is consequently no candidate packet to send for blind verification.

The final run passed 3 test methods containing 163 fault cases:

- 117 publication cases: same-version replacement and host/module-version
  advancement; 78 actual child-process exits before/after operations and 39
  one-shot `ENOSPC` exceptions before operations.
- 34 further process exits during recovery, followed by repeated recovery and
  a simulated next installation-phase launch.
- 12 process exits during stock takeover, legacy symlink migration and an empty
  profile's first installation, including both sides of database commit.

Boundaries include BEGIN IMMEDIATE; SQLite snapshot backup; the first public
staging copy; mod-loader write; filesystem flush; journal write and atomic
replacement; public tree displacement; incoming tree publication; launcher
displacement and link replacement; directory flushes; install_id, host-record and
activation-token writes; COMMIT; journal deletion; and recovery's relocation,
restoration and directory flush operations. Child processes have a 15-second
alarm. Test payloads are tiny synthetic files in disposable temporary directories.

Assertions check the restored or committed runtime and bootstrap, exact mod
loader bytes, launcher resolution, unchanged install_id and unrelated database
keys, preserved older host records, exact pre-update database contents on
rollback, actual installed-byte records after commit, unchanged external mod
payload and settings, and successful subsequent publication. Equicord fixtures
cover the publication matrix; Vencord fixtures cover recovery and takeover.
The fixture mod target already contains a synthetic self-update; the existing
suite additionally exercises a self-update occurring during failed activation.
No fixture runtime, ASAR loader or mod payload is executed.

The required full suite passed **341 Python tests and 75 JavaScript tests**.
The standalone matrix is named `repro_...`, so unittest's default `test*.py`
discovery does not include it: run its explicit command below as well.

## Review reasoning and intended limitations

This section is reviewer reasoning, separate from the executable observations.

At baseline, `integration/updater/installation.py:203-229` documents recovery as
safe to repeat and distinguishes committed activation by a database token.
`installation.py:338-374` journals changes before replacing public names and
flushes directory changes before commit. `installation.py:375-385` handles
exceptions and removes unpublished staging copies. Those boundaries motivated
the explicit process exits: raising an exception alone would test the cleanup
handler instead of simulating a killed process.

The recovery-state review checked the distinct cases of a saved original still
present, an original already restored, a newly created public name with no prior
original, and a committed token with a journal still present. None of those
states produced an installation/database inconsistency in the tested matrix.

Intended behavior was checked against current
[installation recovery](client-updater.md#shared-profile-and-installation-recovery)
(`client-updater.md:147-173`), [mod preservation](client-mods.md)
(`client-mods.md:23-58`), and the historical explanation in
`updater-history.md:421-428,485-489`.
The matching code comments are `installation.py:204-207,233-237,302-317,338-339`.
They promise rollback, record/mod preservation and restart reconciliation; they
explicitly retain backups and generations. No undocumented exception was assumed
to excuse failed recovery. The two assigned exclusions—permanent rejection of
interrupted application launches and retained backup/generation cleanup—were
not reported or investigated as candidates.

Limitations: process-exit and exception injection does not model physical power
loss, torn storage writes, filesystem corruption, unreliable fsync hardware or
all SQLite journal modes. Injection uses the fixture's default SQLite journal
mode. Immutable view preparation is completed before the main publication
matrix; this review does not exhaustively fault every internal copy or metadata
write in prepare_view. The first staged copy represents repeated copy calls.
Recovery is invoked explicitly before verifying the rollback state and then
running publish; this does not launch the full UI/bootstrap chain. Tests do not
model concurrent stock-updater writes, installer mutation during publication or
live Discord use. Those are coverage limits, not speculative findings.

## Commands and evidence

Run from the prepared review worktree. All test, lint and Python commands use
network isolation and uv's offline mode:

```sh
unshare --user --map-current-user --net env UV_OFFLINE=1 uv sync --locked --offline
unshare --user --map-current-user --net env UV_OFFLINE=1 uv run python tests/repro_installation_boundary_matrix.py
unshare --user --map-current-user --net env UV_OFFLINE=1 bun run test
unshare --user --map-current-user --net env UV_OFFLINE=1 bun run lint
```

The existing original checkout's node_modules was symlinked for read-only
dependency reuse. No installation was performed into it. No production profiles,
accounts, credentials, services or media devices were accessed. No external Git
operations, release work or runtime compilation was performed.

Local logs are under `out/installation-review/` in the worktree:
`matrix.log`, `test.log`, `lint.log` and `lint-initial.log`. The initial lint run
flagged loop-variable binding in the new test harness; only that harness was
corrected. The final lint run passed. Formatting was confined to the new files.

The evidence directory is
`/home/he3als/scratch-ai-repo/discord-patched-electron/out/reliability-review-20261030/installation/`.
It contains copies of this report, the standalone matrix and all four logs,
plus `commands.txt` and `baseline.txt`. Shared checklist and ledger files were
not edited. The coordinator's original dirty checkout was not changed outside
the explicitly assigned evidence directory.
