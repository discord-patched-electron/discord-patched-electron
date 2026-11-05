---
name: dpe-dev-environments
description: Use or maintain the DPE development manager for reusable app versions, independent profiles, helper watch/restart, shared runtime/download storage and managed cleanup. Release qualification uses dpe-build-release instead.
---

# Manage DPE development environments

Read the [developer guide](../../../docs/development.md) for commands. Inspect
`bun run dev --help` and current `scripts/dev.py` before relying on options or
reload coverage. Read [implementation and evidence](../../../docs/agent/dev-environments.md)
when changing the manager or investigating storage/reload behavior.

## Choose a reusable baseline

Prefer the manager for iterative helper work instead of making another complete
experiment app/profile under `out/`. Start with `bun run dev list`; inspect the
chosen version's `local-build.json`, `dev-version.json` and client pins rather
than inferring compatibility from its name or historical notes.

Import an existing portable compiled local app when suitable. Import checks its
executable against its local build record, preserves the original and uses
reflinks where available. It does not freshly qualify compiler provenance.
Legacy nonportable apps embed paths and cannot be imported; minimal installed
bootstraps are not full portable local apps either.

For different native inputs, use `create` with a retained verified compiler
artifact and an explicit client lock. Creating a managed version assembles an
app; it does not compile Electron. If suitable inputs are absent, follow
[dpe-build-release](../dpe-build-release/SKILL.md) for artifact reuse/assembly.
Do not interpret a dev task as permission to start a hosted compilation.

Keep managed versions at their permanent paths. Select the profile independently
with `--profile`; use the same dev profile across versions sequentially, and a
fresh profile for automated launch checks. A concurrent run needs a different
version and profile. Launch through the manager so its locks, relaunch handling
and shutdown cleanup remain effective.

## Pick the reload boundary

- `run VERSION` preserves staged helper sources.
- `--sync` refreshes once; `--watch` refreshes and restarts after supported edits.
  Supported sources include integration JavaScript/JSX, CSS, SVG, HTML and Python
  helpers in audio, video, camera and desktop. Check `editable()` and `sync()`
  for the exact coverage; launcher changes require a new version.
- Restart ends active calls/streams. Describe it as watch/restart, not in-place
  hot replacement of loaded voice hooks or native modules.
- Removed sources, updater Python, patch scripts, locks, shaders and native code
  require a new version. Do not copy native binaries into a running app or assume
  that a helper restart applies changed native inputs.

The manager freezes the private dev client's compatibility identity, overlays
helper symlinks and indexes their destinations. Its private installation manifests
also omit mutable logs. These development semantics prevent repeated generation
creation; keep them confined to managed payloads. Preserve normal updater
verification and qualification behavior.

Keep helper links pointed at the staged `integration/audio/` layout with its
packaged `dpe-*` names. Node resolves symlinks before relative imports; linking
straight to `integration/video/` breaks sibling dependencies. Sync must refresh
the packaged aliases as well as their source copies.
After a link-layout migration, verify the published `app-*/modules/` view too.
A repaired download cache can coexist with an intact but stale installed view;
the private dev installation identity must track helper link destinations.

## Control storage and cleanup

Electron objects are read-only hard links, including matching files in managed
profile installations. Resources remain private; prepared modules remain
profile-owned. Discord archives are shared by verified SHA-256. Do not edit shared
Electron files in place or sum per-tree size listings as unique disk usage.

Use `list` for managed inventory and `doctor` for historical `out/` inventory.
Remove only named versions/profiles within the requested cleanup scope. Version
removal preserves profiles; profile removal deletes login data, settings, mods
and client caches. Preview `prune` before applying it; `--downloads` also evicts
reusable archives. Historical output directories and retained compiler objects
are outside managed pruning and need separate review of their owners and uses.

## Validate manager changes

Run `uv run python -m unittest discover -s tests -p test_dev.py -v` while iterating,
then repository lint and full tests as required by `AGENTS.md`. Check locking,
profile preservation, immutable sharing and pruning when changing storage.

For reload changes, use a fresh managed profile and a temporary helper content
edit. Verify restart, unchanged archive cache mtimes and no extra installation
view for that edit. Restore the source afterward. Check desktop launch and
relaunch using the [runtime workflow](../dpe-runtime-checks/SKILL.md); read the
current harness interface before composing the manager wrapper.

Record the actual client/runtime identities, evidence and remaining limits in
agent docs. A managed login-window check does not qualify current native patches,
a live call or a release. Release qualification uses an unmodified portable
payload through [dpe-build-release](../dpe-build-release/SKILL.md).
