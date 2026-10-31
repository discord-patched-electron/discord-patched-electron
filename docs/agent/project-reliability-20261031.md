# Project-wide reliability work, 31 October 2026

Active follow-up to the [initial fixes](reliability-fixes-20261031.md). Work stays
local and fixture-based. Existing changes are preserved; no host profiles,
accounts, devices, release infrastructure or real signing keys are used. The
original interrupted-trial rejection and cache/backup cleanup exclusions remain.

## Updater output framing and cancellation

The existing runner fixture reproduced two defects in
`integration/updater/runner.cjs`:

- A partial JSON record followed by a coalesced pipe read could exceed the buffer
  limit even though every individual record was small. This incorrectly cancelled
  preparation with an oversized-response error. Pipe read boundaries are arbitrary
  and do not represent protocol records.
- Stdout arriving after cancellation continued to be parsed. It could emit late
  progress, grow the buffer until the child exited, and replace the cancellation
  result with an oversized-response error. Buffered output can arrive between
  SIGTERM and process cleanup.

The reader now bounds each individual record, handles multiple records per chunk,
and ignores stdout once cancelled or finished. Oversized individual records still
fail. Process-group termination and the existing timeout remain intact. This
matches the bounded JSON-lines and cancellable-helper design in
[the updater guide](client-updater.md#startup-screen-and-dependencies); it does not
introduce a new protocol or change the approval/execution boundary.

Three added tests in `tests/updater/runner.test.cjs` exercise coalescing across a
partial line, delayed cleanup output after cancellation, and a genuinely oversized
record. Before the fix, the first two fail and the size-limit control passes;
after the fix, all nine runner tests pass. Only mock streams and synthetic helper
processes from the existing fixtures are used.

Evidence: `out/project-reliability/runner-before.log` and `runner-after.log`.
Full-suite and lint output are retained alongside them.

## Release signature binding

The offline release fixture found that `release_signing.verify` checked the GPG
signatures over the Arch package and `SHA256SUMS`, but did not compare the signed
list with the current `release.json` and its assets. A modified manifest paired
with the old valid signature/list therefore passed the complete mocked
`releases.approve(check_only=True)` path. GitHub upload digests matched the changed
fixture bytes; that check establishes upload identity, not signature coverage.
Changing a non-Arch package and its manifest hash also passed the previous checks.

The verifier now requires an unambiguous checksum list covering exactly the
manifest and all its assets, verifies the manifest digest, and checks every
listed payload against both the list and manifest. Signing validates those same
relationships before creating signatures. Explicitly permitted unsigned legacy
verification remains unchanged. This closes a release-tooling approval gap; the
runtime updater already checks manifest signature coverage, so these fixtures do
not establish execution of unapproved code in an installed client.

Real disposable signatures and fully mocked release downloads/API replies prove
the rejection before external approval changes. Controls include a correctly
signed list missing the manifest, missing a package, repeating an entry, naming an
external path or recording the wrong hash. Existing unsigned-legacy and tampering
checks remain enabled. No publication, actual approval or deployed protection was
accessed. The documented chain in [release signatures](releases.md#release-signatures)
requires this binding; no intentional exception for a stale signed list was found.

Evidence: `tests/test_release_signing.py`,
`out/project-reliability/signatures-before.log` (both new binding assertions fail;
the mocked approval previously reports success), and `signatures-after.log`.

## Flags files with invalid encoding

The existing flags fixture reproduced a repair dead end for canonical and legacy
files containing non-UTF-8 bytes. `load_file` decoded before its recovery handling,
so both the repair-editor load and saving a valid replacement could fail. This
contradicted the explicit recovery path and the comment that a valid replacement
must not depend on damaged prior settings.

Normal launch still rejects invalid encoding with actionable recovery guidance.
Explicit repair now shows escaped file contents as comments, with a warning,
rather than activating altered flags. Saving a valid replacement preserves the
canonical file's exact original bytes in the existing backup path; a legacy file
is left untouched. Tests check no write occurs during preview, verify backup
bytes, and confirm the repaired flags survive the next load.

Evidence: two new cases in `tests/test_launch_flags.py`, plus
`out/project-reliability/flags-before.log` and `flags-after.log`.

## Interrupted release signing

An injected failure while signing the second payload left the first detached
signature in the release directory. Every retry then required all signatures and
the public-key asset to exist, so it failed before creating the missing files.
An interruption during publication produced the same dead end. This contradicts
the resumable, immutable-artifact design in the release guide; the existing code
had no partial-set recovery path.

Signing now verifies existing artifacts, prepares missing signatures privately,
verifies them against the pinned public key, and publishes without overwriting
existing files. A subsequent attempt fills in missing artifacts and preserves the
bytes of existing valid signatures. Strict release verification still requires
the complete set; it never accepts a partial set as a release. Invalid existing
signatures and mismatched public keys remain errors.

The existing disposable-GPG fixture reproduces a failure during signing and tests
retry after each of three publication boundaries. It also checks byte-for-byte
preservation and rejection of a damaged existing signature. No real signing key
or release service was used. Evidence: `tests/test_release_signing.py`,
`out/project-reliability/signing-retry-before.log` and `signing-retry-after.log`.

## Discovery recovery and incomplete advisories

The discovery fixture reproduced an activation bug across two actual `main()`
calls with mocked feeds. An Electron feed outage during the first call saved
client state with an empty release ledger. Once the feed recovered, the existing
state file disabled seed selection, while the activation cutoff excluded the
current release. Discovery reported success without tracking that release.

Seed selection now depends on whether a channel has ever been tracked, rather
than whether any state file exists. Recovery preserves the original activation
time and client data. A control confirms that a withdrawn seed still cannot cause
historical backfill. Source verification and enabled-channel filtering remain
required. Evidence: `tests/test_discovery.py`,
`out/project-reliability/discovery-before.log` and `discovery-after.log`.

Separately, synthetic advisory entries with null or non-string patch/range fields
raised `AttributeError` from urgency evaluation, aborting planning before client
work was returned. These inputs now follow the existing unsupported-range path:
they do not grant a cooldown bypass, and client planning continues. Valid advisory
controls still pass. This is an input-robustness correction; no claim is made that
a particular live feed contained these values. Evidence:
`tests/test_update_policy.py`, `out/project-reliability/advisories-before.log` and
`advisories-after.log`.

## Camera preload after relocation

The bundled-app launcher placed the helper's full path in `LD_PRELOAD`. The dynamic
loader splits that variable at spaces, so relocating a bundled app into a directory
with spaces silently disabled fast camera probing. The current cached-installation
branch already handled this correctly. The bundled branch now uses the same
basename plus library-search-directory approach while retaining user preloads.

`tests/test_camera.py` launches the actual shell script with the existing fake
native voice module and fake V4L2 driver under a disposable path containing spaces.
Before the fix, loader warnings and the synthetic ioctl assertion demonstrate that
the optimization is absent; afterward, the existing scoped-probe checks all pass.
The test disables core dumps and accesses no actual devices. This complements the
existing relocation test, which had disabled the camera helper. No documented
exception for relocated paths was found.

Evidence: `out/project-reliability/camera-launcher-before.log` and
`camera-launcher-after.log`.

## Helper descendants and native cancellation

The Python bootstrap waited for its helper leader after SIGTERM but did not finish
terminating descendants if that leader exited first. Normal success/failure also
left any surviving descendants running. The JavaScript runner already handled this
case during cancellation, but its success/failure path had the same gap.

Both paths now terminate the remaining process group when setup ends. Python also
keeps dialog cleanup in a separate `finally` block. This follows the existing helper
ownership and cancellation contract; no background helper is intended to outlive
its setup operation. Real synthetic child processes with private output and a
SIGTERM handler demonstrate the pre-fix leaks and verify cleanup after the fix.
The fixtures have bounded lifetimes and explicit cleanup even when assertions fail.

A separate deterministic process-state fixture reproduces the native dialog closing
as its helper completes between polling iterations. The old loop skipped the last
cancellation check and returned a successful launch path. The bootstrap now checks
the dialog again before consuming the completed result. This affects cancellation
handoff only; it does not mark an application update permanently rejected.

Evidence: `tests/test_runtime_bootstrap.py`, `tests/updater/runner.test.cjs`, and
`out/project-reliability/bootstrap-cleanup-{before,after}.log`,
`runner-cleanup-{before,after}.log`, `bootstrap-cancel-race-{before,after}.log`.

## Archive links after publication

The extractor's resolved-path check accepted a link such as
`files/entry.js -> ../files/value.js`: it leaves the staging directory and re-enters
it by name, so it resolves internally during extraction. Publishing the module
renames that directory to a content key. The unchanged link then points outside
the published module, becoming broken or resolving to an unrelated sibling.

A small Brotli/tar fixture demonstrates both the accepted extraction and the
changed target after rename, entirely inside a disposable directory. The extractor
now checks the link's relative depth from its resolved parent as well as its
current resolved destination. It rejects links that depend on the staging name.
Valid relative executable links and links beneath an internal directory alias
still survive publication. This enforces the existing internal-link requirement;
no intentional exception for links that become external after publication was
found. No claim is made that a live Discord release contains such a link.

Evidence: `tests/test_client_updater.py`,
`out/project-reliability/archive-relocation-before.json`,
`archive-links-before.log` and `archive-links-after.log`.

## Coverage and next work

Current full checks pass: **368 Python tests, 86 JavaScript tests and lint**.
Commands run in the isolated coordinator checkout:

```sh
unshare --user --map-current-user --net env UV_OFFLINE=1 bun run test
unshare --user --map-current-user --net env UV_OFFLINE=1 bun run lint
```

Logs are `out/project-reliability/test.log` and `lint.log`. New regressions use
the existing fixtures, mocked services and disposable files/test keys. No live
release approval, workflow dispatch or compiler allocation was performed.

Initial code inspection mapped updater helper lifecycle, archive extraction,
runtime selection, launcher flag parsing/saving, settings IPC, local splash
assets and module-update notifications to their existing fixtures. Inspection
alone is not a clean bill of health or a confirmed defect.

Settings/flag persistence, release tooling and discovery checks produced the fixes
above. Packaging deliberately requires a fresh output directory; partial-output
rejection was not treated as a defect. Compiler-cache recovery and scheduling
inspection did not justify changing retained objects or build policy.

The native-helper, process-cleanup and archive passes produced the corrections above.
Safe-storage compatibility, desktop/relaunch adapters, pinned-wheel/editor staging,
package generation and client/configuration-input inspection produced no additional
demonstrated defect. Application bundling deliberately uses freshly staged inputs;
the existing update qualification removes the old file index before rebundling.
Next focus: independent verification of the accumulated changes.
No new CI or runtime compilation is authorized by this goal. Native/live behavior
remains outside the offline evidence.
