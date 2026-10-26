# Update discovery and scheduling

Discovery checks for new Discord modules and Electron releases. Client changes
reuse the approved Electron runtime; only native source changes need a compiler.
Passing candidates become public test releases. Manual approval is required for
automatic delivery and AUR publication.

This page covers scheduling and durable state. See [release packaging](releases.md)
for publication and [the updater](client-updater.md) for updates on users' machines.

## Discord updates

`update.yml` polls hourly at minute 22. GitHub schedules can be delayed; manual
**Discover upstream releases** is also available. Only enabled channels are fetched.
Currently that means Canary.

Discovery compares the official distribution manifest, including every module
revision and SHA-256. A changed module counts even when the host version stays
the same. An Electron API outage does not stop Discord discovery.

Each changed client/integration identity starts `release.yml` on the last-good
runtime. Jobs consume the same immutable `ci-state` commit and check native
video/Krisp, application audio, packaging, login/relaunch and official mod installers.
They do not compile Electron. Failed checks retain diagnostics and leave the
previous qualified release available.

When a host archive changes, `inspect_bundled_runtime.py` reads the verified archive
without executing it. It hashes the executable and native runtime payload separately
from ASARs and records an unambiguous embedded Electron version. This detects
shipped binary changes; ordinary Discord fork commits do not schedule compilation.
A changed executable alone does not prove that the bridge needs rebuilding.

For example, the 24 October check found identical bundled Electron 44.9.3 bytes
in Canary 1.0.2357 and 1.0.2365 despite changed client files. The custom runtime
could remain Electron 46.0.0-alpha.1.

## Electron updates

The ledger retains discovered alpha/beta releases, but pending versions are
coalesced into the newest eligible candidate. Routine source advancement waits
**96 hours after the last compiler attempt**, initially measured from the pinned
successful build. No new eligible input means no compilation.

An official high/critical advisory bypasses the wait only when its version range
includes the installed runtime and identifies a fixed candidate on that major.
Unknown ranges and cross-major backports are not guessed. Maintainers can use
`urgent_versions` in `config/update-policy.json` for fixes without suitable public
advisory data.

`advance_electron.py` applies the patch queue in a disposable checkout first.
Failure leaves retained builds and the last-good runtime untouched. Success
advances the source lock with a fast-forward push and dispatches `build.yml`
with the expected native-input key. The compiler verifies that key and reserves
[Blacksmith allowance](ci-setup.md#blacksmith-compilation) before runner allocation.
A healthy compiler is never cancelled or duplicated.

Upstream API changes still need reviewed ports. Source assembly passing does
not establish compilation or runtime compatibility.

## Durable state and artifacts

`ci-state` holds generated JSON and is never merged into `main`. Content keys
reserve work before jobs start, and the ledger retains completed and failed
attempts. Repeated polls do not restart identical failures.

Use Actions **Re-run all jobs** for client work after fixing an infrastructure
problem. Compiler retries need a fresh dispatch and reservation. Source or client
fixes create new integration keys. State writers share a concurrency group and
use fast-forward pushes; do not force-merge JSON state.

`config/update-policy.json` identifies the last-good runtime by compiler run,
source commit, capacity-report digest and archive checksum. A public runtime-cache
release preserves it beyond Actions artifact expiry. Fetching checks both report
and archive hashes. A newer source lock does not invalidate the exact older
runtime used for client checks. New compiler reports/artifacts retain 90 days.

Compilation and client checks have read-only repository access. Source/state
updates, publication and approval receive scoped write permissions. Native keys
exclude launcher, ASAR, mod and audio sidecars, allowing those changes to reuse C++.

## Validate a discovered client without compiling

`native-runtime.yml` and `package-check.yml` accept `clients_state`: a full
40-character `ci-state` commit. Branch names are rejected. Use `canary` for
`discord_channels`; `all` also selects only enabled clients. Omitting the snapshot
uses the reviewed bootstrap locks.

The native workflow can use an existing successful compiler run through
`reference_run`. Without one, it tests the client's bundled runtime as a baseline.
Each selected channel gets its own report. All selected checks run, and any failure
fails the job. Passing an old lock does not clear a new discovery error.

The Arch check builds the generated recipe with an intentionally unusable runtime.
It checks files, dependencies, Krisp patch bytes, metadata and sandbox permissions.
It cannot qualify a runtime for release. Staging trees and dummy packages are
removed; logs, recipes and reports remain.

## Usage and limits

Hourly polling makes 24 scheduled checks per day. Routine unchanged checks do not
upload diagnostics. Temporary release-package artifacts expire after seven days;
public release assets and compiler recovery artifacts have separate retention.

The four-day cooldown and rolling 2,400-minute Blacksmith budget are independent.
An exhausted budget defers source preparation without consuming the candidate.
The compiler still makes its own reservation before allocation.

Read usage without allocating a compiler:

```sh
uv run python scripts/ci_usage.py
uv run python scripts/ci_usage.py --blacksmith
```

The first uses existing `gh` authentication. The organization
[billing API](https://docs.github.com/en/rest/billing/usage) requires Administration
read permission. Standard GitHub-hosted compute for public repositories is
[free](https://docs.github.com/en/billing/concepts/product-billing/github-actions);
storage and Blacksmith are separate. Failed queries report unavailable, not zero.

The second also needs the official
[Blacksmith CLI](https://docs.blacksmith.sh/blacksmith-cli/overview) authenticated
for `discord-patched-electron`. For automation, create an organization token in
your own terminal with `blacksmith org-token create --label ci-usage` and follow
the CLI's stdin authentication flow. Never paste it into chat or commit it.

[Usage reports](https://docs.blacksmith.sh/blacksmith-cli/usage) are observations,
not guaranteed remaining balances. Do not use them to refund reservations.
Usage reporting is on demand and adds no scheduled billing-token secret to CI.

## Earlier evidence

- [Initial discovery](../evidence/discovery-ci.json): two successful polls from
  the 23 October baseline; the unchanged second poll left state untouched and
  allocated no compiler.
- [Local client checks](../evidence/client-updates-local.json) and
  [CI checks](../evidence/client-updates-ci.json): the then-enabled PTB 1.0.224 and
  Canary 1.0.2357 snapshots passed packaging and bundled native/Krisp baselines.
  These do not establish a live call or enable PTB today.

API references: [release pagination](https://docs.github.com/en/rest/releases/releases#list-releases),
[Git refs](https://docs.github.com/en/rest/git/refs#get-a-reference),
[annotated tags](https://docs.github.com/en/rest/git/tags#get-a-tag).
