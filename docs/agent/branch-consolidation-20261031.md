# Branch consolidation, 31 October 2026

The maintainer requested committing all worktrees, merging the remaining work into
`main`, cleaning residual branches and publishing a new prerelease.

All eleven dirty review worktrees were committed before integration. The final
reliability implementation is snapshot `ccfbff39ebb45ec754f830dafe23af73560a00f2`,
described in [the delivery audit](reliability-delivery-audit-20261031.md). Earlier
review snapshots and their reproduction fixtures are preserved as merge ancestors;
their superseded implementations do not replace the final corrections. Previously
squash-merged branch tips are also retained as ancestors before branch deletion.
Worktree files, ignored dependencies, runtime caches and build objects are retained.

Integration preserves the native audio picker and desktop identity fixes already
on `main`, and combines bootstrap trial retry with multi-instance tracking. The
independent rollback regression now writes the launcher's `started-<token>` marker
when simulating a real client crash. Setup interrupted before that marker remains
retryable; a client that starts and exits before readiness still rolls back.

The consolidated tree passes 387 Python tests, 120 JavaScript tests and lint.
Release qualification runs separately against the existing Canary 1.0.2421 client
snapshot and verified Electron 46.0.0-alpha.1 runtime. Historical review counts and
statements about unpublished work describe those earlier snapshots, not this merge.
