# Electron update reliability — 1 November 2026

## Findings and changes

- Source work previously consumed the four-day cooldown before compilation and
  failed identities could remain blocked. Routine cadence now follows actual
  compiler reservations; source/dispatch failures retry with bounded backoff.
- Native failure and successful source-dispatch state were not reconciled.
  The scheduler now tracks compiler requests and reservations independently,
  including lost dispatches, pending jobs, failed builds and successful binaries.
- A branch-level source push raced with client proposal commits. Source updates
  now compare-and-swap the specific lock blob, retry transient conflicts and
  never overwrite another source change. State writes remain fast-forward-only;
  discovery and allowance allocation share a short concurrency lock.
- Security discovery was hourly and waited behind client packaging. A five-minute
  lightweight poll checks releases and all advisory pages. New known fixes bypass
  routine cooldown and can queue immediately, without cancelling active builds.
  Compiler completion and authenticated repository dispatches also wake discovery.
- The fixed 2,400-minute rolling ledger stranded unused free minutes. CI now reads
  current-month organization usage using the pinned, checksum-verified provider
  CLI and an organization token. It uses all 3,000 allowance minutes, including
  shorter final attempts, and settles only provider-reported completed jobs.

## Verified provider contract

Blacksmith CLI 0.5.3 `usage --help` defines `billing_minutes` as platform-weighted
vCPU minutes. The [runner documentation](https://docs.blacksmith.sh/blacksmith-runners/overview)
defines the free tier as 3,000 **2-vCPU** x64 minutes monthly per organization.
The authenticated November 1 API response reported one completed failed job,
16 billing minutes, one runtime minute and USD 0.032 list cost: **8 free-tier
minutes**, matching the maintainer's dashboard. The catalog confirms 16 vCPUs
and USD 0.032/min for this runner. These are observed values, not an estimate
from GitHub timestamps. Job IDs and billed minutes are available from the
paginated `jobs list` API; unreported jobs retain the full reservation.

The [usage API](https://docs.blacksmith.sh/blacksmith-cli/usage) covers all
repositories and runner platforms. [Organization tokens](https://docs.blacksmith.sh/blacksmith-cli/overview#token-types)
are the documented CI authentication method. The repository secret contains the
token; no credentials are committed or put into command arguments.

## Limits

[GitHub schedules](https://docs.github.com/en/actions/how-tos/troubleshoot-workflows)
can be delayed or dropped. Five-minute polling is a requested cadence, not an
instant-detection guarantee. A separately operated webhook/monitor can use the
repository-dispatch trigger; no external monitoring service was provisioned.

[Concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)
can replace pending jobs. Persisted identities plus reconciliation allow a
later poll to recover, while shared state serialization and fail-closed writes
prevent duplicate quota allocation. Exhausted retries remain visible rather
than spending indefinitely on a deterministic compilation/patch failure.

Blacksmith's public interface provides usage observations, not an atomic balance
reservation or account-wide hard spending cap. Unreported jobs in another repo
can race this allocator. This repo reserves before allocation and retains those
reservations until provider reconciliation; other repos need the same coordinator
for an organization-wide guarantee. Missing, stale, inconsistent or malformed
provider data blocks compilation. No paid usage was authorized.

## Validation

Repository lint and 772 tests pass (524 Python, 248 JavaScript), including
provider unit conversion, wrong/stale windows, aggregate lag, month rollover,
partial balances, failure settlement, source CAS conflicts/lost responses,
advisory pagination, bounded retries and duplicate urgent dispatches. Deleted
GitHub workflow history releases the scheduling lease but cannot itself refund
quota; API-reported provider settlement is still required.

[Security-only CI discovery](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/41283591296)
passed with no discovery errors. Its saved state reports 8/3,000 provider minutes,
settles failed run 41282196886, and retains 960 minutes for active run 41282631878.
Client packaging and redundant compilation were correctly skipped. The existing
compiler was left running. No synthetic vulnerability was dispatched to consume
compiler minutes, and a new native build was not required for this CI change.
