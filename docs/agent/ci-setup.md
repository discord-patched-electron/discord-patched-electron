# CI runners

Standard checks and packaging use GitHub-hosted runners. New Electron builds use
Blacksmith through `build.yml`. The old PC and one-shot capacity workflows have
been removed. Retained local source and objects remain available for investigation.

## Blacksmith compilation

The 16-vCPU Ubuntu runner provides 64 GB RAM and 750 GB disk. Smaller runners have
at most 160 GB disk. Ninja selects parallelism from the available CPU and memory,
with one concurrent linker. No sticky disks or paid cache add-ons are enabled.

Before allocating a runner, a GitHub-hosted job reserves **960 allowance minutes**
in `ci-state/state.json`. This covers the entire **120-minute** job at Blacksmith's
8x billing conversion. Compilation stops at 105 minutes to leave time for logs,
artifacts and cache upload. A rolling 31-day cap of **2,400 allowance minutes**
allows two attempts and leaves at least 600 of the reported 3,000 free minutes
outside this workflow. Failed and cancelled jobs keep their full reservation.
Deleting run history cannot recover it. A concurrent state write fails before
runner allocation. Never delete or reset the budget ledger to retry a build.

Retries require a new dispatch, so they reserve their own allowance. Existing
successful runtime identities are rejected before allocation and should be reused
for client packaging. The four-day Electron cooldown is a minimum interval, not a
promise of a new compiler every four days. Client-only packages do not use this
budget. After a budget rejection, dispatch again once old reservations expire.

```sh
gh workflow run build.yml --ref main -f target=prerelease \
  --repo discord-patched-electron/discord-patched-electron
```

This is a conservative project budget, not an account-wide billing limit. Other
repositories, workflows and paid add-ons can consume the organization's allowance.
The last reported usage before this switch was 168 minutes. The dashboard's
free allowance does not establish whether overages are blocked. No fresh hosted
compiler was dispatched for the switch, and cold-build completion within this
limit remains unverified. Budget settings live in `scripts/blacksmith_budget.py`.

The GitHub App needs repository access but no runner API key. `actions/cache`
automatically uses Blacksmith's free dependency cache on its runners. Only the
bounded 3 GiB sccache store is transferred. Full Chromium objects are ephemeral,
and most current Chromium commands bypass sccache. See [cache limitations](build-caching.md#current-full-build-cache-limitation).

Sources: [runner sizes and billing conversion](https://docs.blacksmith.sh/blacksmith-runners/overview),
[free dependency caching](https://docs.blacksmith.sh/blacksmith-caching/dependencies-actions),
[pricing](https://www.blacksmith.sh/pricing).

## Historical local configuration

- Container: `discord-electron-runner`; GitHub runner: `he3als-pc-container`.
- Only host mount: `/mnt/storage/discord-patched-electron-ci` → `/work`.
  The personal home directory and Docker socket are not mounted.
- Current limits: all 20 logical CPUs available, 26 GiB RAM and 36 GiB RAM plus
  swap. The reference and queued prerelease dispatches explicitly use 16 Ninja
  tasks and one concurrent linker. ThinLTO remains disabled.
- Build cache: `/work/build-cache`, outside Actions' disposable checkout.
  Identical native source/configuration/container inputs resume the same reference
  directory even after a driver-only recovery fix. The report records the exact
  driver hashes separately; release identities still include build scripts.
  Changed native inputs create another directory; other builds are never deleted.
- The runner restarts with Docker unless explicitly stopped. It has no host
  services, GitHub login files or AUR credentials mounted.
- The former PC workflow used the `local-compiler` environment and a repository
  runner label. It is no longer dispatched. Pull request checks use standard
  GitHub runners.

Start/stop the runner:

```sh
docker stop --time 60 discord-electron-runner
docker start discord-electron-runner
docker logs --tail 30 discord-electron-runner
```

To stop an active build cleanly, cancel its GitHub Actions run before stopping the
container. Partial object files remain in the dedicated build cache. Dispatching
the same reference workflow again resumes them. A changed build identity creates
a fresh checkout; never automatically delete large caches without checking them.

## Changing local limits while the build runs

Docker applies CPU/RAM limits immediately, without restarting the runner or
discarding compiler objects. For example, to set a six-CPU cap and 16 GiB RAM
with another 8 GiB available as swap:

```sh
docker update --cpus 6 --memory 16g --memory-swap 24g discord-electron-runner
docker stats discord-electron-runner
```

`--cpus` is an aggregate logical-CPU allowance, not affinity. `--memory-swap`
is **RAM plus swap**, so `24g` minus `16g` permits 8 GiB swap. Check current
usage before lowering memory; setting a hard limit below usage can kill compiler
processes. The limits persist across container restarts. Recreating the container
uses `scripts/setup_local_runner.py` defaults (4 CPUs, 12 GiB RAM, 20 GiB combined).

To restore those limits, or inspect the exact saved configuration:

```sh
docker update --cpus 4 --memory 12g --memory-swap 20g discord-electron-runner
docker inspect --format 'CPUs={{.HostConfig.NanoCpus}} RAM={{.HostConfig.Memory}} RAM+swap={{.HostConfig.MemorySwap}}' discord-electron-runner
```

To restore the current unrestricted CPU quota and overnight memory headroom:

```sh
docker update --cpu-quota -1 --memory 26g --memory-swap 36g discord-electron-runner
docker exec discord-electron-runner cat /sys/fs/cgroup/cpu.max
```

The effective quota should read `max 100000`. On this daemon, `--cpus 0` did not
clear the old quota and `NanoCpus` retained a stale value after clearing it;
inspect the cgroup value to confirm what the kernel enforces.

The current Ninja process runs **16 compiler tasks**. Raising Docker's cap
alone does not raise `-j`; changing environment variables cannot reconfigure an
already-running process. Both build workflows default to `parallel_jobs=auto` for a
new dispatch, using CPU affinity, visible cgroup v2 quotas and RAM capacity.
Explicit job counts remain available; `out/capacity/resources.json` records the
decision. Auto is deliberately conservative about per-task RAM. The overnight
dispatches override it with 16 after observing ample memory headroom. After a
failed run has stopped and its failure is fixed, resume with:

```sh
gh workflow run capacity.yml --ref main -f parallel_jobs=16 \
  --repo discord-patched-electron/discord-patched-electron
```

For a candidate, use `build.yml -f target=prerelease -f parallel_jobs=auto`. Job count
does not change compiler flags or invalidate saved native objects. Give the
container enough memory for the chosen concurrency. Do not start Ninja manually
in the active build directory; the workflow serializes compilation.

Source: [Docker's live update command](https://docs.docker.com/reference/cli/docker/container/update/).

## Reference build dispatch

```sh
gh workflow run capacity.yml --ref main \
  --repo discord-patched-electron/discord-patched-electron
```

Each run stops compilation after 21 hours, leaving an hour for reporting before
the 22-hour job deadline and GitHub's 24-hour token lifetime. Resume through a new
dispatch if needed. Sync, dependency installation and hooks have separate completion
markers. Before retrying failed hooks, the driver restores only the dedicated
dependency repositories to their recorded post-sync commits so Electron's `git am`
patch hook can run again. Reports include wall time, sampled minimum free disk, cgroup
peak RAM/swap and artifact checksums. A compiled reference is retained for one day
as a CI artifact and remains in the local cache; it is not a public release.
This first build measures capacity and tests Discord's original fork. It is not
the requested newer-Electron port.

## Recreating the runner

`python3 scripts/setup_local_runner.py` builds the pinned image and registers one
repository runner using an existing authenticated `gh` session. It obtains a
short-lived registration token internally; no API key needs to be pasted into
chat. The script refuses an existing container to prevent duplicate runners.
For another PC, adjust the host workspace and UID first. Remove an old runner in
GitHub settings before replacing it.

The Ubuntu base and downloaded Actions runner archive are pinned by digest.
Runner self-updates remain enabled to keep GitHub connectivity supported.

## Publishing credentials

`AUR_SSH_PRIVATE_KEY` authenticates the AUR account `he3als`. Only the publication
job receives it. `packaging/aur-known-hosts` pins the existing verified AUR host
key; the job requires strict host-key checking and deletes its temporary key file.

`CACHE_TOKEN` is a fine-grained GitHub token with Contents read/write access only
to `discord-patched-electron/cache`. It is passed explicitly to the reusable release
workflow and used only for compiler-cache uploads. App releases use `GITHUB_TOKEN`.
See [release packaging](releases.md) for the approval and AUR update flow.

References: [GitHub runner setup](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners),
[job limits](https://docs.github.com/en/actions/reference/limits),
[token lifetime](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idtimeout-minutes).

## Historical Blacksmith experiment

Run `40001600965` used 168 allowance minutes before failing during tool bootstrap.
That bootstrap issue was subsequently fixed. The single-use workflow has been
removed. Its result did not establish full-build timing or monthly capacity.
