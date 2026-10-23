# CI runners

Repository: `discord-patched-electron/discord-patched-electron`.
Standard GitHub-hosted runners run checks. The maintainer's containerized PC
runner remains available. Blacksmith unlocked the organization on 23 October
2026; the maintainer reports 0/3,000 minutes used and the organization connected.

## Blacksmith capacity experiment

`blacksmith-capacity.yml` is a manual, main-only, single-use experiment. Its
GitHub-hosted preflight rejects any previous dispatch, and both jobs reject
reruns. This intentionally conservative guard needs no billing API credential.
Do not delete its run history: history is the guard's record. It does not track
other repositories or manually deleted runs and is not an account-wide spending
cap. Recurring hosted compilation needs a live allowance check or confirmed
provider-enforced free-only limit before enabling it.

The runner is `blacksmith-16vcpu-ubuntu-2404` (64 GB memory, 750 GB disk), with 12
compiler tasks. Smaller tiers have 80 or 160 GB disk, so they are not the initial
cold-build target. The 300-minute job limit consumes at most 2,400 equivalent
2-vCPU minutes at Blacksmith's documented 8x conversion, leaving 600 of the
reported free allowance as headroom. The command stops after 285 minutes to
leave time for diagnostics. There are no paid caches, sticky disks or static IPs.
The VM's source and object files are ephemeral; a failed experiment does not
automatically start another paid-capable job. The PC retains its own cache.

```sh
gh workflow run blacksmith-capacity.yml --ref main \
  --repo discord-patched-electron/discord-patched-electron
```

The GitHub App integration needs no API key. The first actual run verifies that
the App has repository access. The dashboard's free allowance alone does not
prove that overage is disabled. The build must complete before making any claims
about monthly capacity for all three Electron channels.

Sources: [runner specifications and free-minute conversion](https://docs.blacksmith.sh/blacksmith-runners/overview),
[pricing and paid add-ons](https://www.blacksmith.sh/pricing).

## Installed local configuration

- Container: `discord-electron-runner`; GitHub runner: `he3als-pc-container`.
- Only host mount: `/mnt/storage/discord-patched-electron-ci` → `/work`.
  The personal home directory and Docker socket are not mounted.
- Limits: four CPUs, 12 GiB RAM, 20 GiB RAM plus swap, two compiler tasks.
  ThinLTO is disabled to reduce linking memory for this initial configuration.
- Build cache: `/work/build-cache`, outside Actions' disposable checkout.
  Identical native source/configuration/container inputs resume the same reference
  directory even after a driver-only recovery fix. The report records the exact
  driver hashes separately; release identities still include build scripts.
  Changed native inputs create another directory; other builds are never deleted.
- The runner restarts with Docker unless explicitly stopped. It has no host
  services, GitHub login files or AUR credentials mounted.
- The compile workflow is manually dispatched from `main`, uses a `main`-only
  `local-compiler` environment and a repository-specific runner label. Pull request
  checks use standard GitHub runners; all external contributors require approval.

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

The current Ninja process runs **two compiler tasks**. Raising Docker's cap
alone does not raise `-j`; changing environment variables cannot reconfigure an
already-running process. Both build workflows now accept `parallel_jobs` for a
new dispatch. After the current run finishes (or is deliberately cancelled and
has stopped), an incremental reference build with four tasks can be started with:

```sh
gh workflow run capacity.yml --ref main -f parallel_jobs=4 \
  --repo discord-patched-electron/discord-patched-electron
```

For a candidate, use `build.yml -f target=stable -f parallel_jobs=4`. Job count
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

## Later AUR setup

After runtime and package validation, create or select an AUR maintainer account
and register a dedicated SSH public key. Store the private key in the repository's
`AUR_SSH_PRIVATE_KEY` Actions secret, never in source or chat. Verify the AUR host
key against Arch's published information. Only the separate GitHub-hosted
publication job receives that credential. No submission is appropriate before a
working downloadable runtime exists.

References: [GitHub runner setup](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners),
[job limits](https://docs.github.com/en/actions/reference/limits),
[token lifetime](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idtimeout-minutes).
