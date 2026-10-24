# Build capacity measurements

Initial experiment started 23 October 2026. **Reference build incomplete; capacity is not yet proven sufficient for a
complete compile or the recurring release workload.**

Workflow: [reference build 39997836372](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/39997836372).
Build implementation: `d517391c3ebecfc11e5478ea67fa0c0aa0219e00`.
The standard [checks for that commit](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/39997828518)
passed.

| Setting                | Initial configuration                                                         |
| ---------------------- | ----------------------------------------------------------------------------- |
| Host                   | Maintainer's Arch Linux PC, 20 logical CPUs, about 32 GiB RAM                 |
| Build storage          | `/mnt/storage/discord-patched-electron-ci`, about 651 GiB initially available |
| Isolation              | Dedicated Ubuntu 24.04 Docker container, one workspace mount                  |
| CPU limit              | Four logical CPUs; two concurrent compiler tasks                              |
| Memory limit           | 12 GiB RAM; 20 GiB combined RAM and swap                                      |
| Compiler configuration | Release, no debug symbols, no ThinLTO, no remote execution                    |
| Job bounds             | Compile command interrupted after 21 hours; job limit 22 hours                |
| Recovery               | Persistent source and object files keyed by exact build inputs                |
| Hosted compiler cost   | None; PC electricity/storage use remains local                                |

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

[Attempt 3](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/40001214705)
at `130c58e` confirmed that the stage markers skip the redundant sync and that
dependency patch recovery succeeds. Sentry's optional download was skipped;
Yarn completed, and the Electron sysroots and PGO profiles downloaded. The job
was explicitly canceled during Python environment preparation to hand compilation
over to the newly unlocked Blacksmith account. It did not reach a completed
runtime. Its persistent workspace remains available.

The workflow saves `metrics.json`, `source.json`, `capacity.json` when compilation
finishes, and the build log. Memory peaks come from cgroup counters; disk use is
sampled every five seconds and can miss brief peaks. A canceled job can leave the
last metric marked `running`; the GitHub job conclusion is authoritative. Reports
last seven days, the reference binary artifact one day, and the local build cache
persists. Record final measurements here after the experiment completes.

Blacksmith subsequently unlocked the account. The maintainer reports the
organization connected and 0/3,000 free minutes used. A separate, single-use
Blacksmith experiment uses a 16-vCPU runner, at most 300 real minutes (2,400
allowance minutes), and no paid storage or usage API credentials. Its guard runs
on GitHub before allocating a hosted compiler. See [CI setup](ci-setup.md).
The local cache and runner remain available; neither experiment proves recurring
free hosted capacity until compilation finishes and its cost is measured.

The [first hosted experiment](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/40001600965)
uses commit `f8bdaea`. Its [standard checks](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/40001554946)
passed all 19 tests and all source assembly targets. The workflow passed actionlint,
and synthetic history checks verified that its allowance preflight accepts a first
dispatch and rejects earlier runs even on later API pages. GitHub reports the
Blacksmith App installed and not suspended.

That hosted run failed before C++ compilation, after the fetch, dependency patches
and hooks completed. The depot_tools GN launcher required
`python3_bin_reldir.txt`, but setting `DEPOT_TOOLS_UPDATE=0` also bypassed the normal
Python bootstrap. The driver now explicitly invokes upstream `ensure_bootstrap`,
which preserves the pinned Git revision. This was verified in the local container:
bootstrap exited successfully, `gn --version` returned `2486 (641ace93dd95)`, and
depot_tools HEAD remained `08328c3e239aa5e869da9ac98de765c6541395bf`.

The hosted job ran from 18:15:39 to 18:35:57 UTC (20 minutes 18 seconds). At 8x,
rounding up to 21 minutes estimates 168 allowance minutes. The maintainer has
since confirmed 168 minutes used in the account, leaving 2,832 of 3,000 before
further usage. Its command reported 1,202 seconds, minimum available RAM about 58 GiB
and minimum free disk about 605 GiB. No reference binary was produced, and these
preparation numbers do not establish linker capacity. The one-shot hosted guard
remains in force; the local cache is used to qualify further build-stage fixes.

The corrected [local run 40005436647](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/40005436647)
at `ee31072` successfully completed GN generation and entered actual C++
compilation. The [checks for that commit](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/40005415844)
passed. The local compile is still in progress; compilation/link time and final
resource peaks remain unmeasured.

The v3 candidate toolchain and bounded compiler caches are now implemented;
see [build reuse](build-caching.md). They do not change the running reference
process or discard its incremental output. The original Blacksmith attempt had
no persistent compiler cache; restore/save is wired for a future authorized run,
while its one-shot allowance guard still rejects repeat experiments.
