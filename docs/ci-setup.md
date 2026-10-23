# Local CI runner

Repository: `discord-patched-electron/discord-patched-electron`.
The maintainer selected their PC after Blacksmith suspended the new account.
Standard GitHub-hosted runners still run checks. Compilation runs inside a
dedicated Ubuntu 24.04 container on the PC; no hosted compiler billing is enabled.

## Installed configuration

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

## Reference build

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
