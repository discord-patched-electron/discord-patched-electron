# Qualification recovery — 2026-11-01

Qualification run 41288541838 rejected successful compiler run 41282631878 because
its workflow checked out 589bf9f after the compiler had started on 4c8eb42. The
incremental-cache work changed hashed driver files, invalidating the exact runtime
key even though the native compilation inputs were equivalent.

`config/runtime-compatibility.json` pins this one reviewed capacity report to the
current expected key, original compiler commit and run ID. This is not a global
key alias and does not mark the runtime qualified or approved. Qualification
still runs all native, audio and packaging checks, and public promotion retains
its existing approval gate.

Review evidence:

- No differences from 4c8eb42 to 589bf9f in source locks, imported/compatibility
  patches, assembly implementation, common helpers, compiler-cache implementation,
  build/compiler-cache configuration, the GN toolchain template or runner image.
- `build_config`, `install_toolchain` and `baseline_toolchain` have identical ASTs.
  The GN arguments and Ninja target are unchanged; scheduling, source staging,
  directory choice, checkpointing and metrics changed.
- The completed report has the expected source commit and reviewed assembled
  tree, identical current compiler configuration and all three completion flags.
- Canonical report SHA-256:
  `37defb70f12c23fae2a25e635aab671bd87f8bbeb0ff78159f5b8d2fdcee6b72`.

Changing the current expected key, source/configuration, downloaded report,
compiler commit or run ID makes this exception inapplicable. The binary remains
checked against the report's archive hash. No rebuild or Blacksmith allocation is
needed to rerun qualification against this exact completed artifact.
