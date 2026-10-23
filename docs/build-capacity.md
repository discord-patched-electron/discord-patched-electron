# Build capacity measurements

Initial experiment started 23 October 2026. **Reference build incomplete; capacity is not yet proven sufficient for a
complete compile or the recurring release workload.**

Workflow: [reference build 39997836372](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/39997836372).
Build implementation: `d517391c3ebecfc11e5478ea67fa0c0aa0219e00`.
The standard [checks for that commit](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/39997828518)
passed.

| Setting | Initial configuration |
| --- | --- |
| Host | Maintainer's Arch Linux PC, 20 logical CPUs, about 32 GiB RAM |
| Build storage | `/mnt/storage/discord-patched-electron-ci`, about 651 GiB initially available |
| Isolation | Dedicated Ubuntu 24.04 Docker container, one workspace mount |
| CPU limit | Four logical CPUs; two concurrent compiler tasks |
| Memory limit | 12 GiB RAM; 20 GiB combined RAM and swap |
| Compiler configuration | Release, no debug symbols, no ThinLTO, no remote execution |
| Job bounds | Compile command interrupted after 21 hours; job limit 22 hours |
| Recovery | Persistent source and object files keyed by exact build inputs |
| Hosted compiler cost | None; PC electricity/storage use remains local |

Runner registration, checkout, Chromium 152 fetch, dependency installation and
initial dependency patch application succeeded. Attempt 1 failed after 621 seconds
when the optional `@sentry/cli` installer could not reach its CDN. Its peak cgroup
RAM was about 5.85 GiB, peak swap about 63 MiB, and sampled free disk remained above
631 GiB. These are preparation measurements, not a compile/link capacity result.

The workflow now sets the installer's supported `SENTRYCLI_SKIP_DOWNLOAD=1`; no
symbol upload is performed. [Attempt 2](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/39999471185)
uses commit `00916a6` and the existing build directory. No completed runtime or
compile-time estimate is available yet. Do not extrapolate preparation memory use
to the linker.

Attempt 2 was explicitly canceled after it re-entered `gclient sync` and started
an unnecessary broad Git fetch. Sync and hook completion now have separate markers.
The already synchronized checkout was adopted under its native-input identity
`be92ed7160913c8b53ae06e6003e6de864d292f1199af9420a8c6dd22ec72aa4`,
after checking the old directory's full input digest against commit `d517391`,
the pinned Electron HEAD and absence of compiled output. Electron's recorded
pre-patch Git refs supply the reset bases for hook recovery. No user project or
installed Discord files were reset or removed.

The workflow saves `metrics.json`, `source.json`, `capacity.json` when compilation
finishes, and the build log. Memory peaks come from cgroup counters; disk use is
sampled every five seconds and can miss brief peaks. A canceled job can leave the
last metric marked `running`; the GitHub job conclusion is authoritative. Reports
last seven days, the reference binary artifact one day, and the local build cache
persists. Record final measurements here after the experiment completes.

No paid Blacksmith/Depot resources, paid storage, or usage API credentials are
configured. Blacksmith's account suspension is the reason for the maintainer's
explicit switch to local compilation.
