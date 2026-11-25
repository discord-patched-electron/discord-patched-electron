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
Python AST validation passes. Additional follow-up context edits preserve the
NVIDIA super-resolution default and FFmpeg rolling/import helpers; all remaining
31 Chromium patches apply in sequence in the sparse preflight checkout.
The complete dependency hook queue now applies and GN generation succeeds.
**Full alpha.10 compilation and signed prerelease qualification passed**
after [PC workflow 43546460079](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/43546460079); retained-runtime results above must
not be reported as alpha.10 results. Failed attempts and network recovery are
retained under `out/update-review/`.

The completed alpha.10 runtime key is
`ba2de5a0a4db082ea23845c7776f2f940421544be69a3cc9e949fd730a054161`,
archive SHA-256
`7ae29f857b048149c59ab979c15f606cd62f9fa26ef91a0741dc52b38248d2e1`,
assembled source tree `5437badea3499445c33bb373d388445bb7ddb233`.
`out/update-review/alpha10-runtime/` retains compiler provenance and the verified
artifact. Exact-candidate native bridge/offline Krisp and FrameCapture explicit
stop, immediate stop, target destruction and consumer destruction passed locally.
[Qualification run 43553912380](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/43553912380)
passed bundled/custom native checks, FrameCapture/safeStorage, application audio,
package installs, relocation/relaunch, mod preservation and signed update activation.
The public [alpha.10 prerelease](https://github.com/discord-patched-electron/discord-patched-electron/releases/tag/v20261114035333.0.0-canary.93704cacad66d0b7)
is intentionally unapproved. Local alpha.10 VP8, H.264/OpenH264, AV1/dav1d and
Wayland HEVC/Electron delivery and the full settings harness also passed. The
focus-outline assertion uses computed longhands so serialization order changes
cannot conceal or falsely reject its exact colour/style/width contract. Earlier
retained-runtime results remain distinguished above; account calls, other GPUs,
Windows/macOS, restricted direct GPU and scheduled-presentation limits remain.

An independent main Checks attempt failed to fetch an Arch package signature
from its mirror while building the audio image; release qualification's audio
job passed. Retain this as an infrastructure failure, not a reason to weaken
native or package gates. The failed main job was retried with the same checks and passed in
[main Checks attempt 2](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/43553882488/attempts/2).

### Canary 1.0.2483 module revision 19

The approved `v20261116020335.0.0-canary.cc5107a1f46d6b85` release provides the
revision-12 baseline: source `c97acf3bdc2958abdff109710576899b0255565f`, client state
`5ed96cbd86a0de1ef9ba585ccce00c0f10bdcef6`. Discovery run
[43968870954](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/43968870954)
used source `a1101b3494327352eb30639bcd3be8ab8211796e` and revision-18 state
`bb47dc9588e8c0f2d1603a85367c0136fafb62a4`. Its native/audio gates passed, but the
120-second portable launch check expired shortly after setup restarted.

The subsequent live manifest advertises revision 19. Its retained manifest hash
is `64acfe1b610ff18095000adac409ff418a47c1cffc5ee2af57da6e921492559e` and its
normalized lock hash is
`9bf65d8c9a3f35fa6e5f4cdd7295fa18ef8375358bea23a09ac269a6817f9514`.
Immutable client state `aaba05aab6eab429d62b87df7f5eb4797800c2e4` and the archive
hashes in `config/clients.lock.json` bind its inputs. `out/discovery-20261116/` retains both
immutable state locks, verified archives, complete member/ASAR inventories,
all 11 changed-entry dispositions, text hunks and native comparisons.

All JavaScript, ASAR contents and the voice binary are unchanged. RPC, voice and
spellcheck manifests reorder identical file sets; the generated CLD Makefile
reorders prerequisites. `discord_modules.node` changes only its build identity.
`discord_utils.node` removes an obsolete process-observer callback/constructor
flag, shifts private observer fields, and retains legacy exports as no-ops.
DPE has no private-layout hooks or callers of those deprecated methods; preserve
upstream behavior. Original/prepared voice identities remain the alpha.10 review's
`ae2ca4d...` / `42b32e01...` chain above; no hash-allowlist extension is needed.

Using the verified alpha.10 archive identified above, exact revision-19 checks
passed cold setup/login, real module versions/updater APIs, all four popout
handshakes, same-profile relaunch, native bridge and offline Krisp. Official Vencord and
Equicord install, self-update, package-update preservation, relaunch and uninstall
also passed; the mod fixture removed its profile after the sequence. The revised
launch harness also passed with automatic profile cleanup. The cold path took
97.74 seconds and relaunch 11.81 seconds on this machine; these measurements are
observations, not readiness delays or timeout recommendations.

Revision 18 also stalled in a local preparation check with a 300-second budget;
retain that failed experiment rather than claiming the longer timeout alone
qualifies it. Standalone Node-mode utils teardown crashed on both baseline and
candidate; it does not qualify the Electron renderer lifecycle. Authenticated
game detection, live calls, new codec benchmarks, package-format installation and
Windows/macOS remain unverified in this pass. Existing scheduled-presentation,
direct-GPU and codec limits above remain. This review does not publish or approve
a release.

The fixture-storage/CI follow-up retains exact-input evidence in `out/ci-storage/`.
The revision-19 login, Configure more/onboarding-close path, both launches' four
popout handshakes, full settings harness, frame-capture lifecycle and safeStorage
relaunch passed on the same alpha.10 runtime. Two successive native/Krisp runs
reused the same archive inodes, mtimes and SHA-256 values and left no private
runtime/module/profile trees; `cache-validation.json` and per-fixture storage
receipts record those contracts. Failed experiments and their reports remain
separate from passing results. These checks do not extend the game-detection,
live-call, codec, platform or publication coverage stated above.

### Canary 1.0.2484

Immutable client state `824a4b0faa4ba0be36f9c76fe413a61842418020` matches the
current distribution manifest: host 1.0.2484, voice revision 2, utils revision 3
and revision 1 for the other modules. The comparison in
`out/update-review/canary-latest/` starts from the reviewed revision-19 snapshot
above; the retained revision-12 comparison completes the delta from the approved
release. All archive/member/ASAR unions are inventoried, including the stock
runtime payloads replaced by DPE. Desktop-core and its JavaScript are unchanged.
The bootstrap's executable changes handle macOS updater relaunch; Linux behavior
is unchanged. RPC, spellcheck and voice manifests reorder the same file sets;
the CLD Makefile only reorders existing tokens. Modules/utils code is unchanged
apart from build identities and relocated constant references.

The voice update adds audio retransmission, wire/internal sequence-number mapping
and RTCP NACK translation, with private AudioTransport/LocalUser layout changes.
Preserve those upstream changes: DPE does not access their private fields. The
reviewed codec, soundshare and capture patch functions retain their instruction
bodies and relative target identities. The seven-stage chain, shader and unwind
record are relocated with exact intermediate hashes; picker selection storage and
wake contexts are updated in both Python and the compiled Rust bridge. Original
voice SHA-256 is `041052cf69f4bd551998940f3b0982ec4e1f33182ca53dc6aa319b884ea3f6e8`;
fully prepared SHA-256 is
`b487dfe6d9a524ed7f7a2e15acebe05811e412b55161cfc7075801369c6d0c21`.
`hook-context-proof.json` binds 50 hook/capture/decoder functions, their relative
targets and literals, and the PulseConnect unwind range. Full preparation,
idempotence and picker/scale/sync scope checks pass. The existing reconnect
execution fixture covers both revisions, including failed-context recovery,
locking and stack unwinding. Unknown inputs still fail closed.

Qualification uses the retained alpha.10 runtime identified above; no Electron
compilation is required. Hosted candidate qualification is pending. Live calls,
lossy-network retransmission behavior, physical cameras and cross-platform
behavior are not established by the local preparation and instruction checks.

### Canary 1.0.2510 and Electron alpha.13 candidate

`out/update-20261124/` retains the immutable client snapshot from state
`a683dbb9f6467ccb319c5c2ca87dcd574ff28383`, verified archives,
48 changed member entries, complete ASAR inventories and formatted text diffs.
The comparison starts from the reviewed 1.0.2484 snapshot. The original voice
SHA-256 is `c5e81bfe73be8367b0389d874008a5035e402f8483a47437679778b5b37f9b95`;
the complete prepared module is
`5ad4980d44711716b37e5da190924d609e8d226a64170b7e3f8b16cf0e64516a`.
The ordered patch chain remains idempotent. The capture selection storage and
wake guards move together in Python and Rust; exact canonical instruction,
relative-target and literal comparisons cover 50 hooked capture/decoder functions.
The executable reconnect fixture also covers this revision.

On the retained, verified alpha.10 runtime, the exact candidate passes fresh
setup/login, module APIs, popout handshakes, same-profile relaunch, native frame
bridge and offline Krisp. Synthetic VP8 and hardware AV1 with dav1d reception pass
picker, scaling, synchronization, pacing and cancellation checks. The AV1 paced
case also passes transport reporting. Failed checks with an unprepared module,
software-only display or incompatible capture-mode/transport assertions remain in
the evidence directory. These results do not qualify alpha.13.

Electron alpha.13 is pinned to `d5343c16c17bbab06ea769b0f4b7633e846e4169`.
The source comparison from alpha.10 contains 212 files and 690 hunks. The
compatibility port retains upstream's WebContents point include alongside DPE's
image/range includes. The additive dependency layer adapts video-frame memory
budget includes, GIF configuration and OpenH264 switch context. It preserves
Chromium's removal of FFmpeg audio decoders in the configuration script and the
new CRAS switch declarations. Full dependency patch application and GN generation
pass. Local compilation reuses the retained workspace and eight compile jobs.
The initial 15-CPU quota on the 20-logical-CPU host was reduced in place to one
CPU (5%), then raised to 19 CPUs (95%) at the user's request. The healthy
compiler was not restarted. The memory limit remains 18 GiB.
`out/update-20261125/build-quota-change.json` records these budget changes. Missing Node/npm tools in the container were supplied from a
checksum-verified official Node distribution; retained compile objects survive
those tool failures.

Alpha.13 compilation and runtime qualification are pending. The user's existing
non-Nitro `clock` profile was tested through the full DPE/Equicord startup path
with FakeNitro and its stream-quality bypass temporarily enabled. The native
Optimized picker showed no Nitro banner, and 1080p, 1440p, Source and 60fps were
selectable. Only an owned synthetic source catalog was supplied; no stream,
account call or capture session was started. The test modal was closed, the test
app exited and the original plugin setting was restored. Evidence includes
`clock-nitro-state.json`, `clock-picker-controls.json`, `clock-menu-resolution.json`
and `clock-menu-fps.json`. A repeat using the refreshed helper sources confirmed
“Stream” in the native Optimized picker and unlocked 1440p/60fps on HDMI-A-1;
`out/update-20261125/clock-options.json` and `clock-menu.json` retain that result.
Both native optimized and application-audio primary actions now use “Stream”. The reported locked picker/banner was not reproduced.

Current saved default/global flags also launch both a fresh candidate and the
`clock` profile. The reported default-flags failure remains unreproduced; the
primary running Discord process was not restarted. The same native settings-entry probe passes inside the full DPE/Equicord
`clock` startup: exactly one DPE Settings row precedes Log Out, uses the native
settings icon/type and retains the native compiled parent/layout. Evidence:
`out/update-20261125/clock-native-settings.json`. The signed-out-only legacy
probe still encounters missing lazy CSS modules; its failure is retained as a
harness coverage gap rather than reported as a pass. Account calls, a real Fedora Atomic installation, other GPUs
and cross-platform behavior remain unverified. No release is published or approved.

The long-running 2484/alpha.10 session also produced a SIGSEGV in
`discord::node_api::Environment` bookkeeping inside `discord_utils.node`.
The mapped utils module SHA-256 was
`a2159205be8276cc28e787660721fd49dddb13ec4432f3a170063db9288ece2c`,
which differs from both reviewed comparison and candidate utils binaries.
The fault is a null bucket-list access with a retained bucket count of two in
the static `GetEnvironmentToken::env_count` table. This is consistent with an
invalid table lifetime/state; it does not establish the cause or a link to the
reported startup flags issue. `out/update-20261125/primary-crash-metadata.json`
and `primary-crash-registers.json` retain the native evidence. Only ELF core
notes were read; no heap or raw core was extracted, and the primary profile was
not relaunched. Long-running native-module teardown/lifetime remains an explicit
limit of the bounded synthetic checks.

The subsequent Canary 1.0.2513 snapshot is pinned to state
`683b87868c1f09d7ce58ca77dd50fe187c1dea72`; its verified comparison is retained in
`out/update-20261125/`. Its 21 changed member entries include stricter external-URL
validation in bootstrap and desktop core, stock updater changes, reordered
manifests/Makefile dependencies, Node type-package metadata and native build
identities. Preserve the stricter URL checks; DPE's opener remains behind the
native validation. Modules/utils retain canonical instruction bodies, targets
and literals. The voice binary is identical to the reviewed 2510 binary, so its
complete patch chain and compiled hook scopes apply unchanged. Required native
bridge and offline Krisp checks pass against the retained alpha.10 runtime.
The exact 2513 candidate also passes fresh setup, login-window launch,
popout handshakes and same-profile relaunch on the retained runtime. The isolated
native application-audio test passes selection, cancellation and route cleanup.
Both official mods pass installation, self-update, package-update preservation,
relaunch and removal. Equicord's commit-comparison request is served by the local
self-update fixture before its release response; failed downloads still preserve
the installed bytes. The optional dispatch module's unchanged exported ABI and
OpenSSL dependency set are recorded in the native review; only its exact new
hash is added to the existing two-library exception.

Repository lint and the full Python/JavaScript/Rust suites pass. The settings
harness passes all 46 checks in its isolated X11 environment; host-Wayland hover
checks remain sensitive to focus/window placement. The focused capture-picker
harness passes as well. H.264 VA-API delivery passes with the verified external
OpenH264 decoder supplied, including picker/scale/sync/pacing/cancellation and
transport reporting. The attempt without that decoder correctly failed receiver
delivery and remains in the evidence directory. SafeStorage IPC, invalid
ciphertext and same-profile persistence pass with synthetic data. HEVC VA-API
delivery passes with the Electron receiver; the unsupported native software
receiver attempt remains as a failure. Main-process frame-capture lifetime/GC
passes explicit stop, immediate stop, source destruction and consumer destruction. These results do not establish alpha.13 qualification.
