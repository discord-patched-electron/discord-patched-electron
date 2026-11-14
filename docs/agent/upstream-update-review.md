# Review an upstream update completely

Use this workflow for Discord host/module updates, Electron updates and failures
first exposed by discovery. Fixing the first exception or passing the previous
client lock does not complete the review. Historical snapshot findings are summarized [below](#reviewed-client-snapshots).

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

Store the complete inventory and hunk dispositions in the review's fresh `out/`
directory. Update this reference's qualification summary with immutable inputs,
coverage, artifact location and unresolved limits; do not create a dated report.
Each changed file and behavior needs a disposition; related hunks may share one entry only when their
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
idempotently. Check scope generators, runtime JS guards, compiled helper guards,
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

## Completion

Reconcile every inventory/disposition entry and rerun checks affected by final
edits. Preserve complete review artifacts under `out/` and update the existing
qualification summary with their path, immutable inputs, coverage and open limits.
Leave unrelated changes intact; do not create a separate report or evidence index.

## Discovery and scheduling

`scripts/discover.py` and scheduling/state code distinguish changed content from
poll timestamps. Preserve immutable client snapshots, full source/state commits
and archive hashes. Client/module changes qualify immediately against the retained
runtime; Discord executable bytes detect changes to its bundled Electron, not
fork branch movement. Routine eligible Electron builds wait 96 hours. Verified
high/critical security fixes and bounded retries have explicit policy paths.
Reservations, interrupted jobs and failures remain durable on `ci-state`; do not
merge that generated state branch into development. Unchanged polls do no work.

Compiler dispatch also needs [budget allocation](ci-setup.md#blacksmith-compilation).
An active/successful identical runtime is reused. Source-port conflicts must be
resolved before compilation, not retried blindly. A healthy poll after a failed
qualification does not qualify the failed candidate.

## Reviewed client snapshots

These are historical compatibility evidence, not the current lock or approval.

| Snapshot        | Durable finding / evidence                                                                                                                                                                                                                                                                                                    |
| --------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Canary 1.0.2444 | Native updater migration removed the legacy module-updater binding. Optional chaining on an undeclared identifier still throws. Resolve fallback through the supplied bootstrap module; implement remaining native-updater API paths and exercise same-profile relaunch. `out/client-2444-compat/`, `out/discovery-csp-fix/`. |
| Canary 1.0.2472 | Inventory every payload/ASAR and native diff; port the ordered voice edits and both Python and compiled picker/wake scopes. `out/discord-update-20261107/` contains inventories, ABI review, patch identities and candidate checks.                                                                                           |

The 2472 original voice hash is
`d069bc035b4dab698223a45829e52a096680c76fb8adf0068108acc0a5f8b56e`;
prepared hash is
`3956c8e932bea891ecdf4621fcd4c986bb461460edf875717c934797a021a2a7`.
`scripts/native_client_2472.py` records intermediates. Selection storage moved to
`0x231fb90`; offsets are evidence for that exact module, never a portable recipe.
The port preserved normalized encoder/decoder/clock/picker/wake bodies and matched
shader bytes, with relocated relative operands and all scopes bound to final bytes.

That review reused Electron 46.0.0-alpha.1 from run 41282631878 (review archive
SHA-256 `981f5d9cfcace0b4491807c1a2e9180643ae0d59fe4589e5f513cc890990951c`).
Managed login/relaunch and module APIs, four popout handshakes per launch, native
frame/Krisp, VP8/H.264/AV1/HEVC local delivery and official mod lifecycle passed.
`native/` retains a scheduled-presentation failure: 15 callbacks for 45
submissions. Basic native delivery does not clear it. AV1 without the matching
encoder adapter also failed framing. Do not turn either failure into a blanket
candidate pass or treat later alpha.6 work as part of this review.

`connect-src` blocking `sentry-ipc://start/sentry_key` was a separate preload
Sentry handshake, not a Discord API/module-download failure. No telemetry/CSP
allowance was justified. Recheck exact candidate contents for a new incident.

Discovery also observes all numbered Discord Electron release branches,
independently of upstream release tags and bundled executable hashes. Movement
of the imported branch, a newer major branch, or a missing imported branch is
reported as requiring review in discovery diagnostics. The immutable imported
patch is preserved until a complete source port is reviewed; observing fork
movement never grants approval to replace it. Fork API outages retain the prior
observation and surface a separate error without blocking client discovery.

### Canary 1.0.2483 and Electron alpha.10 candidate

`out/update-review/` retains verified before/after archives, ASAR/member inventories,
47 changed-entry dispositions, formatted JavaScript diffs, ELF export/section
comparisons, native patch/ABI/call-target review and the 238-file/779-hunk Electron
source inventory. The qualified comparison is Canary 1.0.2473: its retained lock
matches the approved proposal at `cda0e901afbb045cded23f9897c751a927655bb3`.
The additional 2472 comparison binds the previous reviewed native chain.
Candidate module revision 11 and all archive identities are in the retained
`after/clients.lock.json` and repository client lock; discovery is a snapshot,
not permission to substitute later same-version module downloads.

Original voice SHA-256:
`ae2ca4d3730dcc3665cc718af1c674662109ad76c8d29f4575f2c50a8d9e6891`.
Prepared voice SHA-256:
`42b32e010c1bbd57bc0eb71b0b751730738d85e282c920f331ffabcb3ef035e5`.
`scripts/native_client_2483.py` binds each intermediate and patch site. Selection
storage is `0x2323c10`; both Python scopes and compiled picker/wake guards carry
the relocated identities. Full preparation is idempotent and unknown layouts
remain rejected. Preserve upstream DAVE fingerprint API changes, ws pong limits,
brace-expansion work limits, normal window bounds and popout spellcheck callbacks;
DPE accepts the sixth popout setup argument without replacing that callback.
The optional dispatch OpenSSL 1.1 exception is restricted to the reviewed new
whole-file hash; it does not establish working dispatch service support.

On the retained verified Electron 46.0.0-alpha.6 runtime, exact-candidate checks
passed fresh-profile login/relaunch and module APIs, bundled native bridge/offline
Krisp, 46 real Settings checks, official Vencord/Equicord installation/self-update/
package-update preservation/uninstall, isolated X11 audio and Wayland video/audio
with PipeWire, PulseAudio and manual selection. VP8, H.264/OpenH264, AV1/dav1d and
HEVC/Electron synthetic delivery passed with picker cancellation, scaler, sync,
transport and decoded-frame integrity checks. HEVC needs the hardware-capable
Wayland receiver; X11 software-only and native software HEVC remain unsupported.
Five package formats passed common payload validation; RPM dependency installation
is explicitly unverified. These results do not establish live-call or other-GPU/
platform parity, nor clear the earlier scheduled-presentation limitation or
qualify the separately restricted direct GPU adapter.

Electron alpha.10 is pinned to
`bdaeaaa4795ee641a6d8262496e028c371533e09`; Discord's `44-x-y` fork still matches
`aade2a4ea35e538d1be93207233c4269459a4561`. Source assembly passes. The
checksum-pinned alpha.10 follow-up adapts AudioVideoTracks context and all six
FFmpeg build-script patches after upstream Python formatting changes. Its Media
Foundation adaptation preserves the newer aligned coded-size/plane-layout fix in
the pool-backed decoder branch and keeps MF buffer ownership separate. The six
complete FFmpeg patches apply in order to the real Chromium pre-patch tree;
Python AST validation passes. **Full alpha.10 compilation and runtime qualification
remain pending** in the opt-in PC workflow; retained-runtime results above must
not be reported as alpha.10 results. Failed attempts and network recovery are
retained under `out/update-review/`.
