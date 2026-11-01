# Blacksmith build economics — 2026-11-01

The user's projected 1,019.97336 allowance minutes corresponds to 127.50 wall
minutes on 16 vCPUs (eight 2-vCPU units). This is an estimate, not measured billing.
Eight builds per month leave 375 allowance minutes per build before retries or
urgent updates. Four-day discovery does not necessarily produce a native build:
it requires a new eligible Electron/native identity; UI-only changes reuse it.

The current workflow's compiler timeout is 105 minutes and its job cap is 120.
A 127.50-minute projection exceeds both. Do not restart a healthy compiler merely
to resize it. The active job 124777515730 had no metrics or logs available through
the authenticated Blacksmith CLI during this investigation, so CPU saturation
and the benefit of smaller runners are not established.

This initial investigation is superseded by [the implemented checkpoint work](free-build-reuse-20261101.md),
including the correction that Electron's upstream cache must not be used for third-party CI.

## Evidence and priorities

- The retained report in `../evidence/prerelease-cache-bypass.json` records 6,755
  sccache requests, zero hits/writes: 5,501 module bypasses and 1,254 unsupported
  Clang flags. This is historical evidence, not live statistics from this run.
  The workflow still uses sccache 0.18.0. Increasing its 3 GiB cache alone will
  not repair unsupported compiler invocations.
- Hosted builds do not retain the complete Ninja output tree. The strongest
  candidate is incremental compilation with retained objects and stable checkout
  paths, on an existing persistent or sponsored runner. Prior workspace relocation
  invalidated module outputs; see [build caching](build-caching.md).
- Investigate Electron's upstream cache-only Reclient access. Non-maintainers can
  authenticate for cache-only CAS, but our custom flags, toolchain and patches
  can reduce hits. Measure compatibility before switching production compilation.
- Compare 8 and 16 vCPUs only after phase/CPU/memory metrics exist. Eight is cheaper
  only if it takes less than twice as long; equal scaling costs the same credits.
  Existing builds already limit symbols, disable ThinLTO and choose concurrency
  from CPU and memory limits.
- The repository is MIT licensed. Blacksmith's OSS program also requires active
  public maintenance and clear community usage; applying could be worthwhile.
  No application or paid resource was created.

Sources checked on 2026-11-01:

- [Blacksmith pricing and OSS program](https://www.blacksmith.sh/pricing): 3,000
  monthly free minutes; sticky disks cost $0.50/GB/month, so not a free cache fix.
- [Runner rightsizing](https://docs.blacksmith.sh/blacksmith-runners/rightsizing).
- [Actions cache](https://docs.blacksmith.sh/blacksmith-caching/dependencies-actions):
  25 GB per repository per week on the free tier; unused entries expire in seven days.
- [Electron Reclient](https://www.electronjs.org/docs/latest/development/reclient).

UI follow-up: native popover invokers fix the filter's second-click dismissal.
Manual dictionary selections now publish a pending state synchronously and keep
it through revalidation until native download, ready or failure events arrive.
The UI says “Starting download…” then “Downloading”; Electron supplies no byte
progress, so no fabricated percentage is displayed. Directory scans retain their
30-second stale-while-revalidate cache; one-second UI polling reads cached metadata.
