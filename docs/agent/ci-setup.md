# CI runners

Standard checks and packaging use GitHub-hosted runners. New Electron builds use
Blacksmith through `build.yml`. The old PC and one-shot capacity workflows have
been removed; retained local sources and objects remain available for investigation.

## Check selection

`check.yml` uses `scripts/ci_scope.py` to select checks for pushes and pull requests.
Lint always runs. Code changes run the full Python/JavaScript unit suite, with
integration jobs selected by their inputs:

| Change                                                                        | Additional checks                            |
| ----------------------------------------------------------------------------- | -------------------------------------------- |
| README, contributor guides, agent notes                                       | None; lint only                              |
| Release notes, approval/AUR/discovery tooling, unit tests                     | Unit suite only                              |
| App settings, updater, package assets and templates                           | Arch packaging                               |
| Launcher                                                                      | Arch packaging and shell syntax              |
| Audio adapters or patches                                                     | Arch packaging and application audio         |
| Krisp patcher                                                                 | Arch packaging and native baselines          |
| Camera support                                                                | Arch packaging and camera binary compilation |
| Compiler cache                                                                | Real compiler cache check                    |
| Native/audio test fixtures                                                    | Their corresponding integration suite        |
| Source/client locks, shared dependencies, selector/workflow, unfamiliar paths | Full checks                                  |

Mixed changes use the union of their scopes. Keep the rules conservative: a new
unlisted path runs everything until its consumers have been identified. Changing
the selector or `check.yml` itself also runs everything. No check here recompiles
Electron; compilation remains a separate budgeted workflow.

The selector compares the complete push range or the PR merge base and head.
It includes deleted and renamed files, without GitHub's API/path-filter file
limits. Missing history or invalid event data runs all checks. A manual **Checks**
dispatch always runs all checks. The run summary lists selected jobs; the selector
log also explains each path's scope.

The **Checks complete** job verifies that every selected job succeeded and every
unselected integration job was skipped. Use this stable check for future branch
protection. Job conditions keep skipped checks from remaining pending, as described
in [GitHub's required-check guidance](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks).

Release qualification in `release.yml` still runs its full packaging, desktop,
native and audio checks before publishing a candidate. Release-publication helpers
(`releases.py`, `release_proposals.py`, `publish_aur.py`) no longer contribute to
the app integration fingerprint: a button or release-note edit should not propose
another app package. Removing these inputs changes the fingerprint once; subsequent
publication-only changes leave it stable.

Replay a historical push locally with full commit SHAs:

```sh
uv run python scripts/ci_scope.py --base BEFORE_SHA --head AFTER_SHA
```

## Blacksmith compilation

A free GitHub-hosted job assembles and validates the source first, retaining a
one-day source checkpoint for transfer. Before allocating a runner, a separate
GitHub-hosted job reads organization-wide usage
through Blacksmith CLI 0.5.3 (version and SHA-256 pinned in the composite action),
then commits its reservation to `ci-state`. Discovery and allocation share a
short state-writer concurrency lock; compilation has its own concurrency group.

| Setting           | Limit                                                       |
| ----------------- | ----------------------------------------------------------- |
| Runner            | 16 vCPUs, rate verified against the provider catalog        |
| Job               | Up to 120 minutes, including 15 minutes for cleanup/uploads |
| Monthly allowance | Full 3,000 x64 2-vCPU minutes per organization              |
| Usage freshness   | At most 5 minutes; rechecked before each allocation         |

The CLI reports platform-weighted **vCPU** `billing_minutes`; divide by two
for the free-tier unit. The allowance covers all repositories and platforms,
not just this compiler. Completed jobs reported by Blacksmith settle their
reservations. Unreported/running/deleted jobs retain their maximum reservation;
GitHub wall times cannot refund it. Monthly rollover does not erase an in-flight
reservation. Smaller remaining balances fund shorter jobs, provided more than
15 wall minutes remain for compilation plus cleanup.

`BLACKSMITH_API_TOKEN` is an organization token stored as an Actions secret.
Missing credentials, unavailable/stale usage, inconsistent aggregates, or changed
runner pricing block allocation. They do not become a zero-usage estimate.
The provider exposes usage rather than a transactional spending-cap API:
concurrent jobs from other repositories can still consume the account balance.
Use this allocator for all organization compiler work to preserve its bound.

Dispatch a new attempt rather than rerunning a compiler job:

```sh
gh workflow run build.yml --ref main -f target=prerelease \
  --repo discord-patched-electron/discord-patched-electron
```

The workflow rejects an active or successful build of the same runtime. Reuse
successful output for client packaging. The separate four-day Electron cooldown
sets a minimum interval for routine new versions; retries and proven security
fixes use the policy in [discovery](discovery.md#electron-updates).

Hosted compilation restores a bounded incremental workspace from the free cache.
Setup/restore time reduces the compiler timeout so the 15-minute checkpoint and
artifact window remains available. See [free build reuse](free-build-reuse-20261101.md).

The compiler preflight is tested without compilation. A complete cold build
within the hosted limit remains unverified. See the
[reliability audit](electron-update-reliability-20261101.md) for provider evidence
and remaining scheduler/account-wide limits.

## Retained local runner

The historical container is `discord-electron-runner`, registered as
`he3als-pc-container`. Its only host mount is
`/mnt/storage/discord-patched-electron-ci` at `/work`. Personal home directories,
the Docker socket, GitHub login files and AUR credentials are not mounted.

Sources and objects live under `/work/build-cache`, outside disposable Actions
checkouts. Different native inputs have separate directories. Driver-only fixes
can reuse a reference checkout; reports record driver hashes separately.
Keep the physical paths and existing objects intact.

The last recorded limits were all 20 logical CPUs, 26 GiB RAM and 36 GiB combined
RAM/swap. Earlier dispatches used 16 Ninja tasks; the completed candidate used 20.
These are historical settings, not a report of a currently running compiler.
The removed `capacity.yml` workflow cannot be dispatched.

Inspect the container before changing it:

```sh
docker logs --tail 30 discord-electron-runner
docker stats --no-stream discord-electron-runner
docker inspect --format 'CPUs={{.HostConfig.NanoCpus}} RAM={{.HostConfig.Memory}} RAM+swap={{.HostConfig.MemorySwap}}' discord-electron-runner
```

## Changing local limits while the build runs

Docker can change CPU/RAM limits without a restart. For an explicitly requested
local adjustment, this example allows six CPUs, 16 GiB RAM and 8 GiB swap:

```sh
docker update --cpus 6 --memory 16g --memory-swap 24g discord-electron-runner
```

`--cpus` is an aggregate allowance. `--memory-swap` means **RAM plus swap**.
Check current usage before lowering memory; a limit below usage can kill
compiler processes. Limits persist across container restarts.

To remove the CPU quota, use `--cpu-quota -1` and check the actual cgroup:

```sh
docker exec discord-electron-runner cat /sys/fs/cgroup/cpu.max
```

An unrestricted quota reads `max 100000`. In the earlier test, `--cpus 0` did not
clear the quota and `NanoCpus` stayed stale, so inspect the cgroup value.

Raising Docker's cap does not change an existing Ninja `-j`. New builds use
`parallel_jobs=auto`, which considers CPU and memory limits. Explicit counts
change scheduling without invalidating native objects. Never start a second
Ninja process in an active build directory or restart a healthy compiler.
See [Docker's update command](https://docs.docker.com/reference/cli/docker/container/update/).

## Recreating a local runner

For an explicitly requested replacement, `uv run python scripts/setup_local_runner.py`
builds the pinned image and registers one repository runner using existing `gh`
authentication. It obtains a short-lived token internally and refuses an existing
container. Adjust the workspace and UID for another PC; remove the old GitHub
runner registration before replacing it.

Defaults are four CPUs, 12 GiB RAM and 20 GiB combined RAM/swap. The Ubuntu image
and Actions runner archive are pinned; runner self-updates remain enabled.
Recreating a runner does not restore the removed capacity workflow.

## Publishing credentials

| Credential            | Used for                                                                                    |
| --------------------- | ------------------------------------------------------------------------------------------- |
| `AUR_SSH_PRIVATE_KEY` | Publishing as AUR account `he3als`; only the publication job receives it.                   |
| `CACHE_TOKEN`         | Runtime-cache uploads; Contents read/write access only to `discord-patched-electron/cache`. |
| `GITHUB_TOKEN`        | Publishing app releases.                                                                    |

AUR publication uses the pinned `packaging/aur-known-hosts` file for strict
host-key checking and deletes its temporary private-key file afterward.

See [release packaging](releases.md) for approval and key maintenance. The initial
Blacksmith experiment, including its 168-minute charge, is recorded in
[build capacity](build-capacity.md).
