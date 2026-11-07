---
name: dpe-native-patches
description: Port or repair DPE native module patches, ABI scopes and compiled hooks, or adapt the imported Discord Electron patch queue to a new source revision.
---

# Port DPE native changes

Choose the relevant layer before editing. A client binary patch, a compiled
sidecar and an Electron source port have different identities and checks. Work
on private staged copies; preserve the imported files and running generations.
Run commands from the repository root.

## Client binary patches and hooks

Start with [the preparation entrypoint](../../../scripts/patch_app_audio.py) and
the affected `scripts/patch_*.py` and `scripts/*_scope.py`. Read the actual current
chain instead of copying stage order or hashes from an old investigation note.

- Verify original whole-file hashes, expected instruction bytes, ELF offsets,
  exported ABI, field layouts, relative call/branch destinations, relocations,
  shader data and cleanup/lifetime contracts. A matching normalized function
  body alone cannot establish compatibility.
- Derive every intermediate and final hash for the ordered patch chain. Check
  re-preparing the complete result is idempotent and unknown input still fails
  closed. Replace private outputs atomically; do not write through an input inode
  that a running process or retained copy may share.
- Review Python scope generation, JS guards and compiled C guards separately.
  Port address tables, selection storage and wakeup hooks as well as body hashes.
  A Python scope accepting a binary does not prove the C helper installs its hooks.
- Trace sidecar inputs through [updater staging](../../../scripts/stage_updater.py),
  [package support files](../../../scripts/generate_package.py),
  [client compatibility](../../../integration/updater/client.py),
  [integration fingerprints](../../../scripts/fingerprints.py) and
  [CI scope](../../../scripts/ci_scope.py). Include changed helpers, shaders and
  assets wherever their consumers are staged or identified.

Add regressions that exercise the changed machine-code path or native contract,
including failure and cleanup. Compile the affected sidecars, then use the
[runtime checks](../dpe-runtime-checks/SKILL.md) or
[capture checks](../dpe-capture-checks/SKILL.md) for the actual changed behavior.
Neither a hash-only test nor a successful compile establishes runtime correctness.

## Electron source ports

Read [source ports](../../../docs/agent/source-ports.md) and the current
`config/sources.lock.json` first. Preserve the exact imported Discord delta;
adapt through the checksum-pinned port layers. Every excluded imported path
needs its explicit replacement or documented upstream equivalent. Do not invent
exclusions from application failures or resurrect removed upstream ownership APIs.

Check fresh assembly against the locked Git tree, dependency patch application,
and the affected translation units with real toolchain flags. Source assembly,
syntax checks, full compilation and runtime validation are separate evidence.
Use [build and release guidance](../dpe-build-release/SKILL.md) if compilation is
necessary; retain healthy compilers and compatible objects.
