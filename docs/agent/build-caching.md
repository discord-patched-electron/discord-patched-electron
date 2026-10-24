# CPU target and incremental builds

The maintainer selected **x86-64-v3** for every published runtime. It includes
AVX2; it does not require AVX-512. Intel Haswell and first-generation Ryzen are
examples of compatible CPU families. Older CPUs and some newer low-end models
are outside this baseline. The AUR architecture stays `x86_64`, with the CPU
requirement stated in each package description.

`config/candidate-build.json` sets `-march=x86-64-v3 -mtune=generic` for the
project-owned `ci/gn/BUILD.gn.in` toolchain. It wraps Chromium's existing Clang
toolchain, preserving its pinned compiler, sysroot and release configuration.
The builder installs it under `src/dpe_build` and selects it through GN's
`custom_toolchain`. It derives local copies of the upstream tool templates with
baseline flags placed before target-specific flags. Arguments remain declared in
the canonical upstream file; the derived templates have private names. Expected
command patterns are checked before rewriting, so an upstream layout change
requires review. No upstream Chromium file is edited.

The flags affect target C/C++, compiler-driven assembly and linking. Host tools,
Rust code and Discord's prebuilt proprietary modules retain their upstream
settings. Upstream CPU dispatch remains intact. There is no `-march=native`,
blanket AVX-512 setting, fast-math override or promise of a measured speedup.
The reference capacity build retains its original configuration; it is a
diagnostic prerequisite, not a publishable candidate.

## Reuse layers

1. **Completed artifact:** the builder checks its input key, successful stage
   markers and archive SHA-256. A match exports the binary without fetching,
   running hooks, generating GN files or compiling. Changed validation status
   alone does not change the runtime key. Discord-only changes reuse the shared
   runtime and invalidate integration/packaging separately.
2. **Incremental Ninja build:** persistent workspaces retain source, prepared
   dependency markers, objects and Ninja state. Resuming the same inputs runs
   Ninja against the existing output directory without cleaning it. Driver fixes
   do not discard the reference checkout. Different native source/configuration
   identities have separate workspaces; source trees are not carried blindly
   between Electron releases.
3. **Compiler cache across releases:** pinned, checksum-verified sccache stores
   compiler results across workspaces. Source-root normalization allows reuse
   when checkout paths change. Compiler, input/header and command-line changes
   still invalidate results. Direct/preprocessor mode is disabled so a newly
   appearing optional header cannot silently reuse a stale result.

Resuming the existing reference workspace also preserves its original unwrapped
compiler commands: inserting sccache mid-build would make Ninja recompile all
existing objects. Fresh reference workspaces and all candidates use sccache.

The PC workflows allow **32 GiB** of local compiler cache, in addition to their
persistent source/object directories. Hosted jobs use **3 GiB** with pinned
`actions/cache` restore/save actions. Hosted snapshots are saved after failed
builds too, when the runner remains available; cancellation/timeouts can prevent
saving. Unique snapshot keys and restore prefixes support incremental updates
to GitHub's immutable caches. CPU/cache policy changes select a new prefix;
sccache checks the compiler inputs within a restored snapshot.

Only the sccache store goes into GitHub's cache, not the Chromium checkout or
whole object tree. GitHub's default free repository allowance is 10 GB and
eviction can discard snapshots. No paid cache expansion, Blacksmith sticky disk
or other add-on is enabled. Cache loss costs time, never changes correctness.
A 3 GiB hosted cache cannot retain every Chromium object; full-build hit rates
and recurring free hosted capacity are still unproven.

Each builder takes an exclusive lock on its compiler store, uses a private
sccache socket, records cache statistics and stops its server before snapshot
upload. All compiler workflows share the `electron-compilation` concurrency
group. Eligible upstream updates dispatch `build.yml`, which accepts prerelease
Electron only. A successful build starts Canary release qualification and public
test-package publication. See [release packaging](releases.md).

## Current full-build cache limitation

Live statistics from the Electron 46 build exposed a limitation that the small
compiler fixture did not cover: all 6,755 requests bypassed sccache.
Of these, 5,501 used `-fmodules` and 1,254 used unsupported `-Xclang` arguments.
There were no cache errors or incorrect hits. The pinned wrapper safely runs
Clang for these commands, but **cross-workspace compiler reuse is not effective
for the current Chromium flags**. Do not infer full-build caching from the
passing small C fixture.

The [sccache documentation](https://github.com/mozilla/sccache#c20-modules)
explicitly excludes Clang header modules. [ccache's module support](https://ccache.dev/manual/latest.html#_c_modules)
requires direct/depend mode and relaxed module checks, whereas this project
currently disables direct mode to catch newly appearing optional headers.
Switching blindly would weaken the tested invalidation contract. Disabling
Chromium modules would change every compile command and sacrifice its existing
cold-build acceleration. The running candidate keeps its current configuration.

Completed-artifact reuse and persistent Ninja objects remain effective: the
reference resumed its partial build and finished successfully. These are the
verified reuse layers for the current full build. Any replacement compiler cache
must first pass representative Chromium commands, module changes, optional-header
creation and cross-workspace invalidation checks. See the [actual build statistics](../evidence/prerelease-cache-bypass.json);
no claim of full-build cache hits is justified yet.

## Verification and current limits

On 23 October 2026, the pinned Chromium Clang passed a real compiler-cache
fixture: one identical repeat hit, and four initial/changed compilations missed
as expected. Changes covered header contents, CPU flags and a newly appearing
optional header. A second server on a different checkout root reused the object
from disk with zero compilations. CPU macros confirmed AVX2/FMA/BMI/BMI2/F16C/
LZCNT/MOVBE and no default AVX-512. The same fixture runs in standard CI using
the hosted image's Clang.

GN generation also passed against a read-only view of the prepared reference
Chromium tree, writing only to a separate overlay/output directory. Its generated
C/C++ rules contained the requested v3 flags. This checks toolchain wiring on
that dependency revision, **not** a full candidate compile or runtime test.
The already-running reference build was not restarted or modified.

The maintainer confirmed **168 Blacksmith allowance minutes used**, leaving
2,832 of the reported 3,000 before further usage. The one-shot hosted experiment
guard remains in place; these changes did not dispatch another hosted compiler.

Sources: [GCC x86 CPU levels](https://gcc.gnu.org/onlinedocs/gcc/x86-Options.html),
[Chromium toolchain extension points](https://chromium.googlesource.com/chromium/src/+/main/build/toolchain/gcc_toolchain.gni),
[sccache local cache correctness](https://github.com/mozilla/sccache/blob/v0.18.0/docs/Local.md),
[sccache path normalization](https://github.com/mozilla/sccache/blob/v0.18.0/docs/Configuration.md),
[GitHub cache limits and eviction](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching).

Native identity calculation now lives in `scripts/runtime_fingerprints.py`,
separate from integration fingerprints. Application-audio wrapper/packaging
changes update integration keys without invalidating the C++ build identity.
The one-time identity implementation migration supersedes old pending candidate
keys through normal discovery reconciliation; it does not restart the already
running reference checkout or discard its Ninja outputs.

## Parallelism and further speedups

New dispatches default to `ELECTRON_BUILD_JOBS=auto`. The scheduling helper
uses CPU affinity and all visible cgroup v2 ancestor CPU/RAM limits; it does not
blindly use the host’s 20 logical CPUs inside a four-CPU container. With four CPUs
and 12 GiB RAM it selects **four Ninja tasks**. Those were the earlier limits;
the overnight run now has all 20 logical CPUs and 26 GiB RAM available, with an
explicit 16-task override based on observed memory headroom.
It reserves 2 GiB and budgets 2 GiB per task, with a 32-task ceiling. This is an
initial heuristic: large individual source files can need more memory. Overrides
accept 1–32 tasks. Docker caps apply live; the selected Ninja task count changes
on the next dispatch. Changing a task count requires a new Ninja invocation; the current reference
run was deliberately resumed with 16 tasks while preserving existing objects.

GN uses `concurrent_links=1` to avoid several memory-heavy links competing with
compilation. This changes Ninja scheduling, not CPU optimization flags. Dependency
sync uses the chosen job count capped at eight. The resource report records the
limits and choices. Scheduling policy is deliberately excluded from the native
key and workspace inputs; the old `config/build.json.parallel_jobs` remains only
for compatibility with the existing reference identity, not as the job default.

Source assembly fetches its selected target and the imported patch’s base (needed
for three-way application); it skips disabled targets and the unneeded fork tip. The
pinned depot_tools commit is fetched only when absent. Three independent Discord
manifest downloads run concurrently. CI already runs source, native, packaging
and audio checks independently; compilers remain serialized to protect their
shared cache and budget. One runtime serves all three Discord clients.

Research checked Chromium’s current build instructions and the pinned
`build/toolchain/concurrent_links.gni` and `get_concurrent_links.py`. Zero debug
symbols and disabled ThinLTO are already configured. A RAM-backed checkout is
unsuitable while this host is swapping. The current instructions’ Git snapshot
cache is a possible future fetch optimization, but it adds a sizeable mirror and
changes the current shallow-fetch approach; it is not required here. No stale
jumbo flag or unproven remote compiler service was enabled. These changes have
not yet produced a measured full-build speedup.

Sources: [Chromium Linux build and speed guidance](https://chromium.googlesource.com/chromium/src/+/main/docs/linux/build_instructions.md),
[Chromium link scheduling](https://chromium.googlesource.com/chromium/src/+/main/build/toolchain/concurrent_links.gni),
[Linux cgroup v2 resource controls](https://docs.kernel.org/admin-guide/cgroup-v2.html).

Local verification of this update passed 67 Python tests, 11 JavaScript tests,
actionlint and all three real-client Arch recipes with a dummy runtime. The
resource helper selected four tasks inside the actual runner. GN generated
33,637 targets in a separate overlay against read-only source and produced a
one-deep link pool. A copy of the real discovery ledger migrated to prerelease
only and was unchanged on the second reconciliation. See
[retained evidence](../evidence/prerelease-scheduling-local.json).

The Electron 46.0.0-alpha.1 candidate now also passes complete dependency patching,
gclient hooks and GN generation in its own prepared workspace (38,464 targets).
Its generated `args.gn` selects the v3 toolchain, sccache and one-deep link pool.
This validates the current port configuration, not yet its C++ compilation.

The first real v3 build found that appending `-march=x86-64-v3` overrode Skia's
separately dispatched v4 code. The derived tool templates now put the baseline
first, preserving target-specific instructions. The previously failing
`skcms_TransformSkx.o` and its module compiled successfully after this change.
Ordinary code still targets v3; the presence of an upstream CPU-dispatched
AVX-512 routine does not require AVX-512 on every machine.

[Actual generated-command checks](../evidence/cpu-dispatch-local.json) confirm the
normal target retains all v3 CPU features without AVX-512, while Skia’s dispatched
variant retains its explicit v4 features.

The frame-capture port restart exposed another incremental-build limit: moving
the prepared source directory changes generated Clang module inputs and rebuilds
their consumers (42,454 tasks in this restart). Keeping the object files alone
does not avoid that work. For future reviewed local migrations, keep the physical
checkout at its original absolute path and point the new identity directory at
it; still verify the source tree, dependency patches and all changed inputs.
Do not rewrite Ninja timestamps or dependency records to force object reuse.
