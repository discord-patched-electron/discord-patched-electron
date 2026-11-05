# Review an upstream update completely

Use this workflow for Discord host/module updates, Electron updates and failures
first exposed by discovery. Fixing the first exception or passing the previous
client lock does not complete the review. The
[1.0.2444 review](client-2444-compatibility.md) is a worked example.

## Pin the comparison

Record the last qualified client snapshot and the candidate snapshot, their
immutable `ci-state` commits, source commits, channel, host/module versions and
archive SHA-256 values. Use the failed run's retained `clients.lock.json` when
investigating a release failure. The bootstrap lock may be older than the last
qualified release; do not silently use it as the comparison baseline.

Fetch an immutable discovery snapshot with the existing validator:

```sh
GH_REPO=discord-patched-electron/discord-patched-electron \
  uv run python scripts/fetch_client_snapshot.py FULL_STATE_COMMIT \
  --output out/update-review/after/clients.lock.json
```

Do the same for the baseline, or retrieve its qualification artifact. Preserve
both locks unchanged. Read the complete failed job logs and retained reports;
identify the failed step and whether a successful later poll actually qualified
the candidate or merely found no new work.

## Inventory and diff every payload

1. Compare the union of all source/module names. Record additions, removals,
   revision changes and archive hash changes, including same-version updates.
   Verify each downloaded archive against its pinned SHA-256 before inspecting
   it. Keep downloads and unpacked copies under a fresh `out/` directory.
2. Inventory every archive member by path, type, permissions, symlink target and
   regular-file content hash. Check both directions, including deleted files.
   Ignore packaging timestamps for content comparison, but do not equate a new
   archive hash with a changed native binary. Read or safely extract archives;
   never run downloaded bootstrap, installer or module code merely to inspect it.
3. Inventory and unpack each changed ASAR and its unpacked companions. Compare
   the complete member union. Do not stop at `bundle.js`: include all preloads,
   frame-view/popout code, bootstrap code, assets, manifests and platform files.
   For example, on a verified, privately staged archive:

   ```sh
   node_modules/.bin/asar extract out/update-review/before/core.asar out/update-review/before/core
   node_modules/.bin/asar extract out/update-review/after/core.asar out/update-review/after/core
   diff -ruN out/update-review/before/core out/update-review/after/core > out/update-review/core.diff
   ```

   `diff` exits 1 when differences exist; that is expected. Treat other failures
   as incomplete evidence. Keep binary files in the content inventory even when
   a textual diff only reports that they differ.

4. For minified text, create formatted comparison copies with the repository's
   pinned formatter. Retain originals and hashes; never format the pinned inputs
   or use formatter output as a shipped patch input. For example:

   ```sh
   node_modules/.bin/oxfmt --stdin-filepath bundle.js \
     < out/update-review/before/core/bundle.js > out/update-review/before/bundle.pretty.js
   node_modules/.bin/oxfmt --stdin-filepath bundle.js \
     < out/update-review/after/core/bundle.js > out/update-review/after/bundle.pretty.js
   diff -u out/update-review/before/bundle.pretty.js out/update-review/after/bundle.pretty.js \
     > out/update-review/bundle.diff
   ```

5. Review every changed text hunk and every added/removed binary or asset entry.
   For Electron, also review the complete upstream source range, dependency rolls
   and the local patch queue. Separate runtime changes from ordinary client or
   integration changes before deciding whether a compiler is needed.

## Keep a complete disposition record

Create an agent note linked from [the index](README.md). Each changed file and
behavior needs a disposition; related hunks may share one entry only when their
coverage is explicit. No entry may remain unreviewed when declaring completion.

| Changed file / hunks                      | Behavior and affected callers    | Disposition                                                                  | Evidence / tests         | Remaining limits             |
| ----------------------------------------- | -------------------------------- | ---------------------------------------------------------------------------- | ------------------------ | ---------------------------- |
| Exact paths and hunk/function identifiers | What changed, including removals | Fix implemented, upstream behavior preserved, or no DPE impact with a reason | Code, hashes and reports | Explicit unverified behavior |

Trace callers on both sides of each boundary: bootstrap/main/preload/renderer,
IPC names and return shapes, updater menus/tray/timers/quickstart/restart,
module lookup/versions/install requests, mod loaders, popouts/overlays,
permissions, telemetry/CSP, packaging and dependencies. A removed listener can
break behavior even when startup succeeds. A renamed API is not automatically
safe just because DPE has no direct call: check the bridges and supported mods.
Preserve intentional upstream fixes, including platform changes DPE does not
need to override. Distinguish blocked telemetry from required application traffic;
do not relax CSP or enable telemetry to clear a console error.

For every changed native module, record original and prepared whole-file hashes.
Review exported ABI, structures and lifetimes, symbols, instruction bodies,
relative branch/call targets, relocations, shader data and every hook's context.
Normalized body equality alone does not establish target identity or ABI safety.
Never add an unknown module to a reviewed hash set solely to bypass a rejection.

When porting a native fix, review the entire ordered patch chain and reapply it
idempotently. Check scope generators, runtime JS guards, compiled C helper guards,
selection storage and wakeup tables independently. Carry new helpers and assets
through source/package manifests, staging, compatibility keys, fingerprints and
CI scope selection. A Python scope accepting a module does not prove that the
compiled helper can install its hooks.

## Implement, review and validate the complete result

Fix all substantiated compatibility issues from the inventory. Add meaningful
regressions for the changed contracts, including errors, unavailable modules,
concurrent requests, cancellation, offline operation and stable running-generation
paths. Preserve the existing failure/rollback policy for unreviewed candidates.
Review the resulting diff again for missed callers, partial ports and changes
that would discard a useful upstream fix.

Run `bun run lint` and `bun run test`, then select integration checks from the
actual affected components. Use the candidate's exact snapshot and the full
working-tree adapters, rather than silently substituting older native-preparation
code to obtain a pass. Validate:

- Fresh-profile login, normal popouts, relaunch and the real exposed module APIs.
- Update triggers, completion/error notifications, preparation and restart.
- Vencord/Equicord install, self-update, package-update preservation and removal.
- Changed native video/audio/Krisp/capture paths, including encode/decode,
  software/hardware receivers, framing and resource cleanup. Capture lifecycle
  checks include source loss/recovery, resizing, FPS changes and cursor composition
  across relay/copy fallback modes.
- Package payloads, staged imports, permissions and required shared libraries.
- Settings through the real settings harness when their components/layout change.

Reuse verified runtimes and retained compiler objects. A client compatibility
failure is not authorization to restart a compiler. Use synthetic sources and
fresh profiles; no unattended account login, messages or calls, physical-camera
recordings, or desktop screenshots during camera tests.

Record exact commands, snapshots/runtime identities and pass/fail reports. Keep
failed experiments as evidence and explain which failures were fixed versus
unsupported environments. Do not claim live-call or cross-platform parity from
synthetic tests. If a relevant check cannot run, report the limitation and do not
claim that behavior was verified. Qualification does not authorize publication
or automatic approval.

## Completion and handoff

Reconcile every inventory/disposition entry, rerun checks affected by final edits
and preserve the evidence under `out/` with its index in `docs/agent/`. Leave
existing user changes intact. If another active thread shares these files,
coordinate edits and recheck after its final changes before declaring the shared
result complete. A requested hibernation must wait for every thread or task the
user explicitly named; an idle/waiting/interrupted run is not proof its work is
finished. Read its final result and verify that required work is complete.
