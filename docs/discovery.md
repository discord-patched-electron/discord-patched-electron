# Release discovery and durable queue

`update.yml` runs on standard GitHub runners hourly at minute 17 and on manual
dispatch. It does not allocate Blacksmith or PC compiler capacity yet. Build,
runtime/audio qualification and publication remain separate unfinished gates.

The workflow reads every page of published release records from
`electron/electron` and `electron/nightlies`. It uses `published_at`, not
`created_at`, and does not assume pages are ordered by publication time. In the
live nightly feed, current releases still have a 2020 creation timestamp.
On activation it records the poll's start time and seeds the highest published
version for each channel. It does not backfill all historical releases. Future
polls retain every published stable and prerelease since that baseline, including
older-major maintenance releases and multiple releases between polls.

Source tags always come from `electron/electron`, including nightlies. A single
Git tag advertisement supplies lightweight refs and peeled annotated refs;
the GitHub commit endpoint verifies newly observed source objects. Every later
poll compares tracked source tags with their pinned commits. A moved, missing,
withdrawn, replaced or retagged release is withheld from the queue, with its old
identity retained. Restoring a moved tag does not erase the anomaly. New version
formats that the parser cannot handle are recorded as issues rather than silently
dropped. A malformed new source object does not prevent healthy releases being
queued; transient upstream API failures abort that poll without partial writes.

The workflow separately downloads all three Discord distribution manifests.
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
reconciliation will be connected with the compiler coordinator. Only **pending
nightlies** can be superseded by a newer nightly; old identities and reasons
remain recorded. Stable and prerelease releases are never coalesced. Pending
work is ordered stable, prerelease, nightly.

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

Tests exercise missed releases, maintenance lines, unchanged polls, native/client
deduplication, annotated tags, moved/replaced/withdrawn releases, invalid source
objects, nightly coalescing, pagination and competing Git state writers.

The first two CI runs succeeded. The second reported no input change and left
the remote `ci-state` commit exactly unchanged, with no compiler job allocated.
The activation baseline is **2026-10-23 19:59:51 UTC**. Both runs discovered
Stable 1.0.166, PTB 1.0.224 and Canary 1.0.2357; the new PTB/Canary revisions
still require runtime/package validation before replacing the bootstrap locks.
See [CI evidence](evidence/discovery-ci.json). The accompanying source, unit,
native-baseline and nine-package checks passed at `c006ba1`.

API references: [release pagination](https://docs.github.com/en/rest/releases/releases#list-releases),
[Git references](https://docs.github.com/en/rest/git/refs#get-a-reference),
[annotated tags](https://docs.github.com/en/rest/git/tags#get-a-tag).
