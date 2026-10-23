# Release discovery and durable queue

`update.yml` runs on standard GitHub runners hourly at minute 17 and on manual
dispatch. It does not allocate Blacksmith or PC compiler capacity yet. Build,
runtime/audio qualification and publication remain separate unfinished gates.

The workflow reads every page of published release records from
`electron/electron`, selecting alpha/beta prereleases only. It does not request
`electron/nightlies`. It uses `published_at`, not `created_at`, and does not
assume pages are ordered by publication time. On activation it seeds the highest
published prerelease and records the baseline. Future polls retain every
prerelease since that baseline, including multiple releases between polls.

Source tags come from `electron/electron`. A single
Git tag advertisement supplies lightweight refs and peeled annotated refs;
the GitHub commit endpoint verifies newly observed source objects. Every later
poll compares tracked source tags with their pinned commits. A moved, missing,
withdrawn, replaced or retagged release is withheld from the queue, with its old
identity retained. Restoring a moved tag does not erase the anomaly. New version
formats that the parser cannot handle are recorded as issues rather than silently
dropped. A malformed new source object does not prevent healthy releases being
queued; transient upstream API failures abort that poll without partial writes.

The workflow concurrently downloads all three Discord distribution manifests.
Channel normalization and binary checksums use the existing lock implementation.
A bad manifest retains that channel's previous lock and records `client_errors`,
allowing healthy channels and Electron discovery to proceed. Consumers must keep
the affected package on its last qualified state until the error is resolved.
Discord-only changes do not create new runtime keys.

## State and deduplication

`ci-state` is an independent branch containing only `state.json`. It is never
merged into `main`, so generated updates cannot create source merge conflicts.
The document records activation, releases and their pinned source identities,
runtime keys/status/attempt lists, current client locks and discovery issues.
It is the durable backlog; the GitHub Actions pending-job queue is not.

Runtime keys include source, compiled patches, CPU/toolchain settings and build
implementation. Identical keys have one record, independent of Discord clients.
A changed native input creates a new pending key. Failed identical keys remain
failed, avoiding endless recompilation; explicit retry and abandoned-run
reconciliation will be connected with the compiler coordinator. Prerelease entries are never coalesced and are ordered by version.
Stable/nightly Electron are disabled in `config/channels.json`. Migration retires
only pending work for those channels, preserving historical release metadata,
attempts and running/completed/failed builds. `pending()` excludes disabled work
even before migration. The reference build can finish normally. Source/build/
packaging entry points reject disabled targets; old locked ports remain evidence.

Canonical JSON excludes poll timestamps except the activation baseline. An
unchanged poll neither changes the state file nor creates a Git commit/push.
Writers share one Actions concurrency group. Git pushes are fast-forward only;
a racing writer fails instead of merging JSON or overwriting another update.
The next poll reads the current branch and reconciles the upstream feeds again.

Only the discovery job receives GitHub contents-write permission, for this state
branch. Credentials are supplied through the job environment and Git's `gh`
credential helper, never embedded in a remote URL or state file. No AUR key or
provider API key is needed for discovery. API retries are bounded to three
attempts for timeouts, rate-limit responses and server errors.

## What this does not yet publish

The candidate compiler workflow currently builds the reviewed channel locks.
Connecting durable queue entries to compilation, immutable runtime manifests,
integration validation, GitHub Releases and AUR transactions remains work in
progress. The queue alone does not prove a release is compatible or publishable.
No AUR recipe is advanced from a discovery status or an unvalidated binary.

Tests exercise missed prereleases, disabled-channel migration, unchanged polls, native/client
deduplication, annotated tags, moved/replaced/withdrawn releases, invalid source
objects, preserved running work, pagination and competing Git state writers.

The first two CI runs succeeded. The second reported no input change and left
the remote `ci-state` commit exactly unchanged, with no compiler job allocated.
The activation baseline is **2026-10-23 19:59:51 UTC**. Both runs discovered
Stable 1.0.166, PTB 1.0.224 and Canary 1.0.2357; the new PTB/Canary revisions
subsequently passed the selected-channel checks described below.
See [CI evidence](evidence/discovery-ci.json). The accompanying source, unit,
native-baseline and nine-package checks passed at `c006ba1`.

API references: [release pagination](https://docs.github.com/en/rest/releases/releases#list-releases),
[Git references](https://docs.github.com/en/rest/git/refs#get-a-reference),
[annotated tags](https://docs.github.com/en/rest/git/tags#get-a-tag).


## Validate a discovered client update without compiling

`native-runtime.yml` and `package-check.yml` accept `clients_state`, the immutable
40-character commit of `ci-state`, and `discord_channels`, a comma-separated
selection such as `ptb,canary` (default `all`). Both fetch and retain exactly that
snapshot and its provenance. A branch name is rejected. Omitting the commit uses
the reviewed bootstrap locks. Discovery errors remain in the provenance; passing
a retained older lock does not authorize advancing an affected client channel.

The native workflow accepts an existing successful compiler run through
`reference_run`. It checks the selected clients against that downloaded runtime;
without it, it checks their official bundled runtimes as a baseline. Neither
workflow compiles Electron. Each selected channel receives a separate report,
and a failure does not prevent the remaining channels from being tested. The
job still fails if any selected check fails. A subset pass never implies that
unselected clients passed.

The Arch workflow shares the same packaging implementation as the main Checks
workflow. It builds the prerelease-Electron recipe for each selected client
with an explicitly unusable dummy runtime. This verifies private installation
paths, native library dependencies, preserved Krisp patch bytes, metadata and
sandbox permissions, but cannot qualify a real runtime. Staging trees and dummy
package archives are removed after each attempt, including failures; recipes,
logs and reports are retained.

Local validation of the exact discovered PTB 1.0.224 and Canary 1.0.2357 snapshot
passed all six then-enabled packaging combinations. The current scope has one
prerelease-Electron package per client. Both clients also passed native frame
handoff/teardown and offline Krisp processing using their bundled Electron
44.9.3, with 53.6 dB synthetic noise reduction. These are baseline results, not
custom-runtime, live-call or per-application audio qualification. The existing
narrow dispatch-library exception required no change. See
[local evidence](evidence/client-updates-local.json). The CI snapshot checks subsequently passed;
[CI evidence](evidence/client-updates-ci.json) records both runs. The bootstrap
locks now include those exact PTB/Canary snapshots.
