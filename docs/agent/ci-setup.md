# CI runners

Checks and packaging use GitHub-hosted runners. New Electron compilations use
Blacksmith through `build.yml`; package jobs never compile Electron.

## Check selection

`check.yml` uses `scripts/ci_scope.py` to select checks for pushes and pull requests.
Source lint always runs. Documentation changes skip native package installation
and Cargo checks. Code changes run Python, JavaScript and Rust tests, with
integration jobs selected by their inputs:

| Change                                                                        | Additional checks                                                           |
| ----------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| README, contributor guides, agent notes and skill instructions                | None; source lint only                                                      |
| Release notes, approval/AUR/discovery tooling, unit tests                     | Unit suite only                                                             |
| App settings, updater, package assets and templates                           | Arch packaging                                                              |
| Launcher                                                                      | Arch packaging (shell syntax is already covered by lint)                    |
| Audio adapters or patches                                                     | Arch packaging and application audio                                        |
| Krisp patcher                                                                 | Arch packaging and native baselines                                         |
| Camera support                                                                | Arch packaging and camera binary compilation                                |
| Capture producers, hooks, protocols and their build scripts                   | Arch packaging, native baselines and independent capture bundle compilation |
| AppImage builder, dependency bundle and dedicated fixtures                    | AppImage build and launch without FUSE                                      |
| Shared release packager or AppRun                                             | Arch packaging and AppImage                                                 |
| Compiler cache                                                                | Real compiler cache check                                                   |
| Native/audio test fixtures                                                    | Their corresponding integration suite                                       |
| Source/client locks, shared dependencies, selector/workflow, unfamiliar paths | Full checks                                                                 |

Mixed changes use the union of their scopes. Keep the rules conservative: a new
unlisted path runs everything until its consumers have been identified. Changing
the selector or `check.yml` itself also runs everything. No check here recompiles
Electron; compilation remains a separate budgeted workflow.

Only `agents/skills/*/SKILL.md` is classified as skill documentation. Executable
helpers and other new skill resources keep full checks until explicitly scoped.

AppImage validation is a job in `check.yml`, selected from the same complete diff
as Arch packaging. There is no separate AppImage push/PR workflow or path filter
to keep synchronized. Its dedicated builder changes select AppImage rather than
Arch; shared package creation and AppRun changes select both.

The **Checks complete** job requires every selected check to pass. A manual
Checks dispatch runs all checks. To inspect a commit range locally:

```sh
uv run python scripts/ci_scope.py --base BEFORE_SHA --head AFTER_SHA
```

## Workflow ownership

Keep a workflow boundary when its inputs, permissions or lifecycle differ. Share
stable tool installation through `.github/actions/setup-project`; keep system
packages and sandbox rules with the job that needs them. The Arch job deliberately
installs only Bun on the host; Python and compilers live in its container.

| Workflow               | Responsibility and reason to retain it                                                                                                                                                                                           |
| ---------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `check.yml`            | One push/PR entry point, complete-diff selection, source/unit checks, source assembly, helper compilation, Arch/AppImage and selected runtime/audio checks; one required completion gate.                                        |
| `native-runtime.yml`   | Reusable exact-client native video/Krisp checks; custom runtimes also exercise frame lifetime and safeStorage. Bundled and custom runtime checks protect different compatibility boundaries.                                     |
| `app-audio.yml`        | Reusable isolated X11/Wayland capture and audio checks. PipeWire and PulseAudio cover different supported servers; the manual picker covers a separate selection path.                                                           |
| `package-check.yml`    | Reusable Arch recipe build with a dummy runtime; catches recipe/container failures without compiling Electron.                                                                                                                   |
| `update.yml`           | Discovery, durable scheduling and immutable client snapshots; polling is operational work, not a redundant test suite.                                                                                                           |
| `build.yml`            | Source preflight, paid compiler budget, checkpoint recovery and runtime artifact production. Preserve these steps and healthy retained objects.                                                                                  |
| `import-runtime.yml`   | Verify and import a locally compiled runtime without allocating a hosted compiler.                                                                                                                                               |
| `release-channels.yml` | Fan out successful imported/compiled runtimes to enabled client channels.                                                                                                                                                        |
| `release.yml`          | Qualify the exact candidate and package formats, fresh-profile relaunch, mods and signed update activation before publication. Standalone dispatches need their own lint/tests; an earlier PR run does not qualify these inputs. |
| `approve-release.yml`  | Explicit approval of already tested signed artifacts, followed by AUR publication.                                                                                                                                               |
| `publish-aur.yml`      | Retry AUR publication of an already approved release without repeating approval or rebuilding.                                                                                                                                   |

Do not merge approval into discovery or compilation just to reduce file count.
Do not replace real install/launch/update tests with assertions about workflow
text. Container setup, checksum verification, sandbox exceptions, budget checks
and diagnostic uploads each support a retained execution boundary.

## Test design and coverage

Use the existing runners: Python `unittest`, JavaScript `node:test` with strict
assertions, and Cargo. `bun run test` runs all three sequentially, reports every
suite's failures and exits nonzero if any fail. Sequential execution avoids
concurrent native fixture builds sharing Cargo outputs. JavaScript discovery
uses `tests/**/*.test.cjs`; Electron fixture entry points remain outside that
naming pattern. Python discovery uses `test_*.py`.

Choose tests by the failure they detect, not by a target count or blanket rule
that every edit needs a new test:

- Exercise observable results, recovery, ownership and invalid inputs. Keep tests
  for update atomicity/signatures, native ABI bounds/lifetimes, selector fallback,
  content identities, package installs and sandboxing even when they are costly.
- Patch-generation text assertions can protect exact byte edits or fail-closed
  upstream layouts. Prefer executing the patched fixture where practical. A
  string assertion alone does not establish desktop or native behavior.
- Do not test that a script contains a particular glob, function name or markup
  fragment when executing that script/suite already covers the requirement.
  The removed `test_fixture_markers_are_staged_with_native_receiver` only checked
  the spelling of a `package.json` glob; it neither staged nor ran a receiver.
  Recursive discovery and the actual receiver tests cover the intended behavior.
- Extend a nearby behavior test before adding a second fixture for the same
  contract. Avoid whole-workflow snapshots, duplicate syntax checks and tests for
  reversible copy/style edits. Do not delete distinct positive/negative controls
  because their setup looks similar.
- Use `t.mock` and its automatic teardown for JavaScript dependencies and clocks;
  use `unittest.mock`, temporary directories and cleanup hooks for Python.
  Advance a mock clock for scheduling assertions. Real process/IPC/native tests
  still need readiness handshakes, bounded waits and guaranteed cleanup.
- Synthetic native fixtures prove their exercised ABI/ownership contracts, not a
  real GPU, portal, camera or call. Settings mock tests do not prove layout or
  focus parity; use the settings harness for those changes. Candidate checks
  must use the candidate snapshot, not merely the bootstrap locks.

A library migration needs a specific missing capability. Adding pytest, a second
JavaScript runner, a homegrown scheduler or a coverage percentage gate does not
by itself improve these boundaries. Coverage gaps should be stated as missing
behaviors and addressed with the appropriate fixture or end-to-end check.

## Failure-history triage

The 2026-11-12 CI review inspected the latest 100 runs and available failed-job
logs (2026-11-04 through 2026-11-12). Local raw evidence is under `out/ci-review/`
(`runs.json`, `<run-id>.log`); hosted links below identify the retained inputs.
This sample is not a coverage measurement and does not prove hosted fixes until
new workflows execute.

- Local verification exposed a KMS descriptor-count race: `strace -f` showed
  libtest opening `/sys/devices/system/cpu/online` while the DRM fixture counted
  `/proc/self/fd`. The count changed despite correct resource cleanup, then the
  failed assertion poisoned the shared test mutex. Count aliases of the fixture's
  resource instead: named memfd exports for DRM and the transferred socket's inode
  for rejected requests. Keep positive live-descriptor and post-cleanup assertions;
  do not disable parallel tests or relax leak checks to a permitted range. The
  failing full-suite log, isolated reproducer and syscall trace are in
  `out/ci-review/tests-final.log`, `kms-repeat.log` and `kms.strace`.
- [Compiler recovery run 43222756115](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/43222756115)
  and repeated attempts fail in `git am --abort` because the runner has no
  committer identity. Recovery now supplies command-scoped identity. The regression
  creates a real conflicted mail patch with global/system identity disabled and
  checks that recovery preserves an untracked compiled object. It fails before
  the fix. This fixes recovery, not the original upstream patch conflict in
  [run 42057144790](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/42057144790).
- [Run 43114815671](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/43114815671)
  rejects an unreviewed H.264 VA-API native module in audio qualification, while
  package qualification also fails its launch checks. Keep those gates; resolve
  the candidate through the complete upstream review. Passing local bootstrap-lock
  tests does not resolve this candidate.
- [Run 42415765822](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/42415765822)
  exposed an unavailable PipeWire header constant and a read-only HEVC build path
  in the Arch container. The current workflow mounts `out/` writable. Retain both
  Ubuntu compilation and Arch packaging: they catch different host assumptions.
- [Run 42418384745](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/42418384745)
  failed encoder scope and native call-shape assertions. These are meaningful
  contract failures, not evidence to delete native tests.
- [Run 42436865424](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/42436865424)
  failed the Wayland fixture; later startup changes address cold enumeration.
  Native runtime, initial bootstrap and module API failures also appear elsewhere
  in the sample. Keep isolated launch/relaunch and audio diagnostics to distinguish
  timing/setup failures from regressions; do not add blanket retries.
- [Run 42583651023](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/42583651023)
  includes an external AppImage-tool download failure; discovery
  [run 42393622829](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/42393622829)
  fails an HTTP request with 403. Tool and API availability are separate from test
  quality. Preserve checksum checks and surface infrastructure failures.

Read the failing step's final result, not every occurrence of “error” in its log.
Tests intentionally print rejected patches and simulated failed state pushes;
those messages followed by `ok` are not the run's cause. Some older failed runs
have unavailable logs, and several runs were cancelled by newer pushes. Neither
supports a claim of a flaky test. See the [upstream review](upstream-update-review.md)
for unresolved native candidates and [runtime validation](runtime-validation.md)
for real desktop checks.

## Blacksmith compilation

Reuse a verified runtime first. New attempts require source preflight, fresh
organization-wide usage and a budget reservation in `ci-state`. Missing/stale
usage or credentials block allocation. The allowance is 3,000 x64 2-vCPU minutes
per month; existing reservations and other repositories also consume it.

The 16-vCPU job has a 120-minute limit, including 15 minutes for checkpoints and
uploads. Routine new Electron versions wait 96 hours; verified security fixes
and bounded retries follow [discovery policy](upstream-update-review.md#discovery-and-scheduling).
Healthy or already successful builds of the same identity are not restarted.

For an authorized new attempt, dispatch rather than rerun an old compiler job:

```sh
gh workflow run build.yml --ref main -f target=prerelease \
  --repo discord-patched-electron/discord-patched-electron
```

Preserve retained local workspace paths and objects. Inspect an existing compiler
before changing limits; never run a second Ninja in its directory. Detailed usage
accounting, local-runner commands and credential scopes are in the
[budget details](#compiler-budget-detail). Only change a retained runner for a current task.

## Compiler budget detail

Blacksmith CLI usage is weighted vCPU `billing_minutes`; divide by two for the
free-tier accounting unit. Usage must be no older than five minutes. Completed
provider-reported jobs settle reservations; missing/running/deleted jobs retain
their maximum reservation, including across month rollover. GitHub wall time
cannot refund them. Missing credentials, stale/inconsistent usage or changed
pricing block allocation. Other repositories can still consume the shared account
balance; the provider has no transactional spending-cap API.

`BLACKSMITH_API_TOKEN` belongs only to the allocator. `CACHE_TOKEN` has Contents
write access only to the runtime-cache repository. `AUR_SSH_PRIVATE_KEY` belongs
only to publication, with `packaging/aur-known-hosts` and temporary key cleanup.
Signing uses `RELEASE_SIGNING_KEY`; do not move credentials into compiler jobs.

For a currently authorized local-runner adjustment, inspect container logs,
`docker stats`, actual cgroups and the active Ninja first. Docker's
`--memory-swap` is RAM plus swap; reducing it below current use can kill work.
An updated CPU quota does not change an existing Ninja `-j`. Never start a second
Ninja in the same output or restart a healthy compiler. Historical container
names, machine inventory and dispatch commands are not current authorization.
