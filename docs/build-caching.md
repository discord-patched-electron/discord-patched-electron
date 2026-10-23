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
`custom_toolchain`. This requires no edit to an upstream Chromium file.

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
group. Candidate builds are manual until runtime/release gates are complete;
`build.yml` selects stable, prerelease or nightly and automatically triggers
native validation against all three Discord channels after success.

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
