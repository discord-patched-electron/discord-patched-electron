# CPU target and build reuse

Published runtimes require **x86-64-v3**: AVX2, without an AVX-512 requirement.
Intel Haswell and first-generation Ryzen are examples of compatible CPUs.
Older CPUs and some newer low-end models do not meet this baseline. Arch package
metadata uses `x86_64` and states the extra requirement in the description.

Reuse a verified runtime before considering compilation. Completed archives and
retained Ninja objects work; sccache does not currently provide effective reuse
for the full Chromium build.

## Toolchain

`config/candidate-build.json` sets `-march=x86-64-v3 -mtune=generic` through
`ci/gn/BUILD.gn.in`. The project toolchain wraps Chromium's pinned Clang, sysroot
and release configuration under `src/dpe_build`, selected by GN's `custom_toolchain`.
It derives private templates without editing upstream Chromium files.

Baseline flags go before target-specific flags, preserving CPU-dispatched code
such as Skia's v4 routines. Expected command patterns are checked before rewriting.
A changed upstream layout requires review.

The flags affect target C/C++, compiler-driven assembly and linking. Host tools,
Rust and prebuilt Discord modules keep their upstream settings. There is no
`-march=native`, fast-math override or measured speedup claim. The historical
reference build keeps its diagnostic configuration.

## Reuse layers

1. **Completed runtime:** matching native inputs, successful stage markers and
   archive SHA-256 allow export without fetching, hooks, GN generation or compilation.
2. **Ninja objects:** the same inputs resume the existing output directory.
   Hosted compatible updates use stable slots and Git/gclient transitions; local
   builds retain their existing per-input layout by default.
3. **sccache:** pinned compiler results can cross workspace boundaries when the
   command is supported. Compiler, header and flag changes invalidate results.
   Direct/preprocessor mode is disabled to detect newly appearing optional headers.

Native keys are calculated in `scripts/runtime_fingerprints.py`. Launcher,
ASAR, mod and audio-adapter changes affect integration identity, so they can reuse
Electron. Test changes can rerun validation against an existing artifact.

Capture helpers also belong to the integration identity. Changes to the native
capture adapter, direct Wayland producer, Vulkan/OpenGL hooks, protocol XML,
helper build scripts or scope metadata rebuild the affected helpers and app
package while retaining the same Electron runtime and compiled objects.
CI builds and retains the capture bundle separately as `capture-support`; the
native adapter remains in the independent `camera-support` artifact.

Build either helper group locally without an Electron checkout or compilation:

```sh
uv run python scripts/build_camera_support.py --output out/camera-support
uv run python scripts/stage_capture_backends.py --output out/capture-support
```

Use a fresh output directory for the capture bundle. Packaging verifies reusable
bundles with `--prebuilt` and reuses the existing verified runtime. The runtime
fingerprint intentionally excludes these helper sources and build scripts;
regression tests check both runtime reuse and package invalidation for each input.

Do not insert sccache into an existing unwrapped build: changing its compiler
commands makes Ninja rebuild objects. Keep physical source paths stable too.
Moving the prepared Electron 46 checkout once caused **42,454 tasks** to rerun
because generated Clang module inputs changed. A reviewed identity migration can
point to the original directory, after verifying all inputs. Never rewrite Ninja
timestamps or dependency records to force reuse.

## Current full-build cache limitation

The Electron 46 build sent 6,755 requests through sccache; all bypassed it:
5,501 used `-fmodules`, and 1,254 used unsupported `-Xclang` arguments.
Clang ran normally, with no cache errors or incorrect hits. See
[the build statistics](../evidence/prerelease-cache-bypass.json).

[sccache excludes Clang header modules](https://github.com/mozilla/sccache#c20-modules).
[ccache's module support](https://ccache.dev/manual/latest.html#_c_modules) needs
relaxed checks that conflict with this project's optional-header test. Disabling
Chromium modules would change all compiler commands. Neither is a drop-in fix.

A replacement cache must pass representative Chromium commands, module changes,
optional-header creation and reuse across workspaces before adoption. A small C
fixture passing does not establish full-build cache effectiveness.

## Storage and concurrency

Hosted builds now retain bounded source/tools/Ninja checkpoints in Blacksmith's
free dependency cache. Fixed paths and nanosecond file/directory timestamps allow
Ninja reuse without relaxed compiler-cache checks. Compatible updates use a stable
workspace slot; major/toolchain boundaries remain separate. Failed builds save
progress before the reserved deadline when possible. Checkpoint eviction or an
upload failure can still cause a cold build.

The compressed cap is 20 GB, below Blacksmith's default 25 GB cache capacity.
No paid sticky disk or cache expansion is enabled. The sccache wrapper remains
pinned and bounded to 3 GiB, but its store is no longer uploaded as the main reuse
mechanism. Existing local workspaces keep their original identity paths unless
`ELECTRON_INCREMENTAL_WORKSPACE=1` is explicitly selected. No existing local
objects are moved or deleted.

See [free build reuse](free-build-reuse-20261101.md) for measurements, recovery,
archive validation, the router assessment and provider limitations.

## Parallelism

New dispatches use `ELECTRON_BUILD_JOBS=auto`. The helper considers CPU affinity
and visible cgroup v2 CPU/RAM limits. It reserves 2 GiB, budgets 2 GiB per task
and caps the result at 32 tasks. Four CPUs with 12 GiB RAM select four tasks.
Explicit overrides accept 1–32; large files can still require more memory.

GN permits one concurrent linker, and dependency sync uses at most eight jobs.
The resource report records the limits and chosen task count. Scheduling changes
do not alter compiler flags or native identity. Docker caps apply immediately;
Ninja concurrency changes only on a new invocation.

Source assembly fetches the selected target and required patch base. Pinned
depot_tools objects are fetched only when missing. Independent checks run in
parallel, while compilation stays serialized. Zero debug symbols and disabled
ThinLTO are already configured; further speedups need measurements.

## Verification

- [Compiler-cache fixture](../evidence/compiler-cache-v3.json): real Clang repeat
  hit, changed-header/CPU/optional-header misses, and reuse from another root.
- [Scheduling and GN checks](../evidence/prerelease-scheduling-local.json): cgroup
  limits, one-deep link pool, generated v3 rules and unchanged discovery state.
- [CPU dispatch](../evidence/cpu-dispatch-local.json): ordinary code retains v3
  without AVX-512; Skia's dispatched variant retains its explicit v4 features.
- [Completed runtime](../evidence/prerelease-runtime-local.json): full compile and
  subsequent runtime validation, superseding the earlier source-only checks.

References: [GCC CPU levels](https://gcc.gnu.org/onlinedocs/gcc/x86-Options.html),
[Chromium toolchains](https://chromium.googlesource.com/chromium/src/+/main/build/toolchain/gcc_toolchain.gni),
[sccache correctness](https://github.com/mozilla/sccache/blob/v0.18.0/docs/Local.md),
[path normalization](https://github.com/mozilla/sccache/blob/v0.18.0/docs/Configuration.md),
[Chromium build guidance](https://chromium.googlesource.com/chromium/src/+/main/docs/linux/build_instructions.md),
[cgroup v2](https://docs.kernel.org/admin-guide/cgroup-v2.html).
