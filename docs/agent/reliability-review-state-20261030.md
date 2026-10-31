# Offline state reliability review, 30 October 2026

Baseline and final HEAD: `f90b251caee6b4e7d265c0e450e53a70d97b0aba`.
Worktree: `/home/he3als/.t3/worktrees/discord-patched-electron/t3-a7df7fe6`.
Scope: `integration/bootstrap/loader.py`, `integration/updater/client.py`, and
`integration/updater/runtime.py`; adjacent callers were read to establish triggers.
No production code was changed. These are candidates awaiting independent verification.

All three REPRODUCTION tests **pass on unchanged production code by asserting
wrong behavior**, using [the existing fixtures](../../tests/test_repro_state_reliability.py).
Shared artifacts are under
`/home/he3als/scratch-ai-repo/discord-patched-electron/out/reliability-review-20261030/state/`.
`blind-claims.md` and the test copy can be given to a verifier without this report.
The reasoning below is deliberately separate from that handoff.

## S1: Interrupted client publication strands complete pinned bytes offline

**Severity: medium. Invariant: I8, offline recovery of an authorized cache.**

Trigger: cancel or terminate first setup after the complete client generation has
been renamed into place but before `current.json` is published. Subsequently use
explicit offline mode. No module corruption or missing approval is necessary.

The test injects a process-interruption exception at the pointer write, after all
four synthetic modules and the generation have passed validation. It establishes
that the generation matches the actual package pins through the real
`approved_sources` implementation. Offline selection nevertheless raises
`First launch requires an internet connection`. Changing only `offline=False`
recovers precisely that generation, with network access and further module
installation both forbidden by mocks. Offline selection succeeds afterward.

Expected: under the lock, locate and verify the generation implied by the package
pins and compatibility key, then recover its pointer without network access.
Observed impact: an interrupted setup blocks explicit offline startup even though
every required module is already cached and authorized. This is recoverable by a
normal-mode retry, not data loss, not permanent application rejection.

Baseline locations:

- `integration/updater/client.py:415-419`: only the pointer supplies `current_host`.
- `integration/updater/client.py:452-457`: offline mode rejects a missing pointer
  before computing or validating the package-pinned generation.
- `integration/updater/client.py:519-522`: generation publication and pointer
  publication are separate, creating the reachable interruption boundary.
- Reproduction: `tests/test_repro_state_reliability.py:38`.

Intended-limitation check: `docs/agent/client-updater.md:91-95` requires package pins
or retained approval for offline use; this fixture has exact package pins. It only
requires online repair for **unapproved** caches. The docstring at
`client.py:141-145` also includes offline use in the package-pins gate, and the
legacy-cache comment at `client.py:328` explicitly preserves complete offline
clients. `docs/agent/updater-history.md:259-262` discusses online recovery and a
runtime interruption fix; it does not designate complete pinned clients as
intentionally unusable offline. This candidate concerns client pointer recovery,
not the excluded loader trial-rejection behavior.

Suggested correction: allow offline discovery of an exact authorized generation
before rejecting a missing pointer. Preserve signature/pins checks, module
integrity, compatibility checks, and the no-download rule. Do not accept arbitrary
unreferenced generations just because they have metadata.

## S2: Runtime permission damage bypasses validation and startup repair

**Severity: medium. Invariant: I8, usable current runtime and damaged-cache repair.**

Trigger: the execute bit on a previously verified cached Electron is lost, for
example when restoring a profile cache with a file-copy/archive tool that restores
regular files without Unix executable permissions. File bytes remain intact.
This is a cache-damage recovery case, not an update that spontaneously changes mode.

The test seeds the existing tiny runtime ZIP through checksum verification and
extraction, with the executable probe mocked. It removes execute permission on
the disposable cached executable. `validate_generation` accepts it. Offline,
bootstrap, and ordinary selection return the nonexecutable path without calling
the repair installer. The real `bootstrap.prepare` also returns it and never
starts its preparation helper. `os.access(..., os.X_OK)` is false throughout.
No runtime code is executed to demonstrate the failure.

Expected: reject an unlaunchable cached executable before returning it, then enter
the existing repair/error path. Observed impact: normal launch obtains an unusable
path and cannot open its startup UI. Repeating launch does not trigger repair.
Restoring permissions or removing/reseeding the cache is required. Disabling
automatic update checks in this fixture isolates repair from release selection;
the normal bootstrap path always does offline cache selection first anyway.

Baseline locations:

- `integration/updater/runtime.py:290-305`: validates hashes and required files,
  but not execute permission.
- `integration/updater/runtime.py:315-322,469-474,503-504`: returns that generation
  as usable without a probe/repair.
- `integration/updater/bootstrap.py:18-23`: successful offline selection bypasses
  preparation; `integration/launcher/discord-launcher.sh:62-65` then tries to run it.
- Reproduction: `tests/test_repro_state_reliability.py:81`.

Intended-limitation check: `docs/agent/client-updater.md:110-119` promises damaged
cache repair and requires an intact cache offline. The history at
`docs/agent/updater-history.md:520-523` records repair of damaged runtime generations.
The cache-selection comment at `runtime.py:327-328` concerns identity reuse, not a
permission exemption. There is no mode check or documented permission-damage
limitation in the scoped runtime code. The parallel application-layer check in
`loader.py:181-182` and its existing missing-launcher-permission regression show
that executable mode already matters to this project's launch validation.

Suggested correction: check executable access in runtime generation validation.
Route permission damage to repair; consider restoring the owner execute bit only
after complete pinned-file validation. Do not treat content hashes alone as proof
that an executable can launch. No need to execute arbitrary code to check mode.

## S3: A surviving second instance is invisible to application activation

**Severity: medium. Invariant: I1, preserve running application generation.**

Trigger: launch two instances with the supported `--multi-instance` switch or
saved Multi Instance setting; stage an approved application update; close the
first instance while keeping the second open; launch another instance.

The reproduction drives `loader.main` through a deterministic three-PID/start-time
model. The first instance reaches modeled readiness. The second receives the
same application and `--multi-instance` at the mocked exec boundary. Only the
first PID remains recorded. A second generation is prepared with real disposable
GPG signatures and approval checks. When only the first PID disappears, the next
launch selects the new application despite the modeled second instance remaining
alive. No launcher or payload code is executed.

Expected: a surviving instance continues to pin the running application until it
also exits. Observed: the pending update reaches the exec boundary while the old
application still has a live instance. Impact is premature application activation
and simultaneous application versions sharing one profile. This test does not
claim observed data loss, a live Electron crash, or mutation of running files.

Baseline locations:

- `integration/bootstrap/loader.py:360-371`: assumes second launches merely
  forward and returns before recording another live process.
- `integration/bootstrap/loader.py:423-440`: selects pending code and replaces the
  single running-process record after the originally tracked PID exits.
- `integration/updater/startup.cjs:44-49`: permits an independent instance when
  `--multi-instance` or the saved preference is enabled.
- Reproduction: `tests/test_repro_state_reliability.py:134`.

Intended-limitation check: `docs/agent/client-updater.md:59-66` explicitly says second
launches retain the running generation. `docs/agent/updater-history.md:343-347`
records multi-instance preference preservation. The pinned settings page labels
Multi Instance experimental (`integration/updater/openasar/config.html:256`), but
current startup code explicitly supports it; neither the docs nor loader comments
declare an exception allowing application activation underneath surviving
instances. The loader comment at lines 368-369 instead assumes all second
invocations forward and exit. The candidate is confined to that mismatch; ordinary
single-instance forwarding is not alleged to fail.

Suggested correction: track all actual application process identities, pruning
exited/PID-reused records, or use a shared lifetime lease held by each running
instance. Defer activation while any prior-generation instance survives. Preserve
same-process startup handoff and short-lived forwarding behavior.

## Validation and boundaries

Read `CONTRIBUTING.md`, `docs/agent/README.md`, and the shared checklist first.
The initial worktree was clean and HEAD matched the requested cached baseline.
Dependencies were provisioned with `uv sync --locked --offline`; local Bun
dependencies use a symlink to the authorized original `node_modules`, used only
for reads. All test/lint commands use
`unshare --user --map-current-user --net env UV_OFFLINE=1`.

Commands and full output are retained as `commands.sh`, `reproduction.log`,
`test.log`, and `lint.log` in the shared state directory. The baseline suite had
341 Python/75 JavaScript tests. With these three reproductions, validation passes
344 Python/75 JavaScript tests and lint.

Covered through code review, existing fixture tests, and new reproductions:
application staging and readiness, ordinary second-launch forwarding,
same-process startup handoff, malformed metadata, failed verification, cancelled
initial setup, package upgrades, runtime cache reuse across application paths,
offline reuse, client publication interruption, and runtime file/mode damage.
The mandatory full suite also exercises adjacent synthetic components; it is not
a separate review of the approval, audio, or installation areas.

No optional scope expansion, agents, production fixes, external Git operations,
release/CI operations, or Electron compilations were performed. Tests use
disposable profiles, tiny existing synthetic archives/modules, disposable signing
keys, and mocked process/exec boundaries. There were no account, production
profile, credential, device, or desktop-capture accesses. Existing mandatory tests
execute their own synthetic programs; new reproductions never execute payloads.
Concurrency was bounded to the existing four module workers and a three-process
identity model. This does not establish physical power-loss durability or actual
multi-instance Electron lifecycle behavior. The known permanent update-rejection
and uncollected-directory findings are excluded.
