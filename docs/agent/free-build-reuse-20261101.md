# Free build capacity and incremental checkpoints — 2026-11-01

## Decision

Keep Blacksmith's verified free allowance and 16-vCPU runner while collecting
phase-level measurements. Use free GitHub runners to assemble and validate the
Electron port before reserving Blacksmith time. Persist the actual source/tools/
Ninja workspace in the free dependency cache, with a 20 GB compressed cap.
No paid sticky disk, sponsorship application or router runner was enabled.

The source-only artifact measured 102,763,882 bytes locally. It has one-day
retention and replaces source assembly on the compiler runner on a cache miss.
The existing checks, packaging, camera binary and runtime validation already use
standard GitHub runners. Full Chromium compilation on those runners is not yet
qualified: their documented disk allocation is 14 GB, versus this project's
100 GB cold-build preflight. Do not silently weaken that guard or assume unused
preinstalled SDK space will always be available.

## Actual cache, rather than bypassed compiler requests

The retained completed candidate workspace measured approximately 36 GiB,
including 6.4 GiB of Ninja outputs. An archive including its shared Git metadata
and depot_tools compressed to **13,456,173,452 bytes in 218 seconds**, with zstd
level 3 and two compression threads on the desktop. These are local measurements,
not a hosted-runner speedup or billing estimate.

Checkpoints use fixed `/tmp/dpe-electron-build` paths on hosted runners. Within
a compatible slot, Electron updates retain object files; Git/gclient update the
source and Ninja decides which inputs changed. Major releases, compiler config,
imported fork and toolchain boundaries have separate slots. A write-ahead marker
makes source transitions recoverable after interruption. A failed source port
never starts a compiler or overwrites the previous valid source.

The archive preserves nanosecond file **and directory** mtimes: GN treats some
globbed directories as inputs. Chromium's absolute FFmpeg links are accepted only
when they resolve inside the exact cache root. Other escaping links, device files,
path traversal, checksum/size mismatch and physical-path mismatch are rejected.
OS dependency installation is repeated on a new VM; source hooks and outputs
remain reusable. The build and checkpoint writer share an exclusive cache lock.

The cache is best effort, not durable storage. Eviction, an oversized archive or
an upload failure can require a cold build. The 20 GB cap fits one snapshot under
the provider's 25 GB capacity; it does **not** promise retention of multiple
snapshots or multiple majors. Once a replacement is packed and verified, retention
deletes only our managed `ninja-v2` main-branch keys before uploading it. This
avoids requiring room for two 13 GB snapshots. A failed replacement upload can
therefore lose the previous cache; successful runtime artifacts remain separate.
Unrelated caches and other branch scopes are never deleted.

The pinned Actions cache v2 implementation does not
have the legacy client's 10 GiB upload check. No paid cache expansion is enabled.

The compiler's deadline subtracts restore/setup time and preserves the existing
15-minute cleanup window. Checkpoint packing has an eight-minute cap and failures
cannot invalidate an otherwise successful runtime. The 3 GiB sccache store is no
longer uploaded as the main reuse mechanism; its historical module/flag bypasses
made it ineffective for this build. The wrapper remains pinned and bounded.

`phases.json` records dependency sync, OS installation, hooks, GN and compiler
wall times and host CPU busy percentages, including failures. Use these with
memory data before choosing 8 versus 16 vCPUs. Eight only reduces allowance use
if its runtime is less than twice the 16-vCPU runtime for equivalent inputs.

## Router assessment

Read-only inspection found that the proposed router has 8 GB installed RAM but
less than 3 GB available, with existing services already using swap. Disk space
is sufficient for a cache, but the full build's 8 GB memory budget would compete
with routing and existing workloads. No packages, containers, limits, firewall
rules or services were changed. Keep host-specific inventory out of public CI.

## Correction to the upstream-cache suggestion

Electron's general Reclient documentation mentions non-maintainer cache access,
but its maintainers explicitly clarified that this is for Electron contributions,
not third-party CI infrastructure. Do not enable their shared backend for DPE.

## Sources

- [Electron maintainer clarification](https://github.com/electron/build-tools/issues/807#issuecomment-3964608067).
- [Blacksmith dependency cache](https://docs.blacksmith.sh/blacksmith-caching/dependencies-actions):
  free, default 25 GB per repository per week; stale cache entries are evicted.
- [Sticky disks](https://docs.blacksmith.sh/blacksmith-caching/dependencies-sticky-disks)
  and [pricing](https://www.blacksmith.sh/pricing): paid at $0.50/GB/month.
- [GitHub standard runners](https://docs.github.com/en/actions/reference/runners/github-hosted-runners):
  public-repository compute is free; standard Linux runners have 4 CPUs, 16 GB RAM
  and 14 GB documented SSD space.

Local verification artifacts are under `/mnt/storage/dpe-build-economics-20261101`.
Keep timing claims limited to those measurements; the next hosted execution must
confirm provider upload/restore behavior and the actual cross-version hit rate.

## Verification results

- Full retained workspace extracted in an isolated Ubuntu container at its original
  physical paths. After restoring directory timestamps, Ninja's dry run reports
  `no work to do`, matching the untouched original workspace. No compiler ran.
- Fresh real Electron source assembly, 102.76 MB compressed snapshot, restore at
  the same path and Git/index/source-report verification passed.
- Regression fixtures cover checksum and path rejection, size limits, internal
  symlinks, file/directory timestamps, compatible slot selection, interrupted Git
  transitions, retained objects and scoped cache retention.
