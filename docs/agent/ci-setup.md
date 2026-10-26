# CI runners

Standard checks and packaging use GitHub-hosted runners. New Electron builds use
Blacksmith through `build.yml`. The old PC and one-shot capacity workflows have
been removed; retained local sources and objects remain available for investigation.

## Blacksmith compilation

Before allocating a runner, a GitHub-hosted job reserves the full job allowance
in `ci-state/state.json`. Settings live in `scripts/blacksmith_budget.py`:

| Setting                 | Limit                                                 |
| ----------------------- | ----------------------------------------------------- |
| Runner                  | 16 vCPUs, 64 GB RAM, 750 GB disk                      |
| Job                     | **120 minutes**; compilation stops at **105 minutes** |
| Reservation per attempt | **960 allowance minutes**, using the 8× billing rate  |
| Rolling budget          | **2,400 allowance minutes** over **31 days**          |

Stopping compilation early leaves time for logs, artifacts and cache upload.
The budget permits two attempts and leaves 600 of the reported 3,000 free minutes
outside this workflow.

Failed and cancelled jobs keep their reservation. Deleting runs or resetting the
ledger must not recover allowance. A concurrent state write fails before runner
allocation. After a budget rejection, wait for reservations to expire.

Dispatch a new attempt rather than rerunning a compiler job:

```sh
gh workflow run build.yml --ref main -f target=prerelease \
  --repo discord-patched-electron/discord-patched-electron
```

The workflow rejects an active or successful build of the same runtime. Reuse
successful output for client packaging. The separate four-day Electron cooldown
sets a minimum interval; it does not promise a build every four days.

This is a project budget, not an account-wide billing cap. Other repositories
and services can consume allowance. The last reported usage before this policy
was 168 minutes. A complete cold build within the new limit remains unverified.
Use [usage reporting](discovery.md#usage-and-limits) to inspect current consumption.

The GitHub App needs repository access, without a runner API key. Only a bounded
3 GiB sccache store is transferred through `actions/cache`; Chromium objects are
ephemeral. Most current Chromium commands bypass sccache. See
[cache limitations](build-caching.md#current-full-build-cache-limitation).
No sticky disks or paid cache add-ons are enabled.

References: [runner sizes and billing](https://docs.blacksmith.sh/blacksmith-runners/overview),
[dependency caching](https://docs.blacksmith.sh/blacksmith-caching/dependencies-actions),
[pricing](https://www.blacksmith.sh/pricing).

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
