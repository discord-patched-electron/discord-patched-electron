# Build capacity measurements

Initial experiment started 23 October 2026. **Build in progress; capacity is not
yet proven sufficient for a complete compile or the recurring release workload.**

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

Runner registration, checkout and Node setup succeeded. The initial fetch is
running against the locked Discord 44 fork and Chromium 152. No completed runtime
or compile-time estimate is available yet. Do not extrapolate from the fetch's
memory use to the linker.

The workflow saves `metrics.json`, `source.json`, `capacity.json` when compilation
finishes, and the build log. Memory peaks come from cgroup counters; disk use is
sampled every five seconds and can miss brief peaks. A canceled job can leave the
last metric marked `running`; the GitHub job conclusion is authoritative. Reports
last seven days, the reference binary artifact one day, and the local build cache
persists. Record final measurements here after the experiment completes.

No paid Blacksmith/Depot resources, paid storage, or usage API credentials are
configured. Blacksmith's account suspension is the reason for the maintainer's
explicit switch to local compilation.
