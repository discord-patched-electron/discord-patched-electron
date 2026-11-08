# CI runners

Checks and packaging use GitHub-hosted runners. New Electron compilations use
Blacksmith through `build.yml`; package jobs never compile Electron.

## Check selection

`check.yml` uses `scripts/ci_scope.py` to select checks for pushes and pull requests.
Lint always runs. Code changes run the full Python/JavaScript unit suite, with
integration jobs selected by their inputs:

| Change                                                                        | Additional checks                                                           |
| ----------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| README, contributor guides, agent notes and skill instructions                | None; lint only                                                             |
| Release notes, approval/AUR/discovery tooling, unit tests                     | Unit suite only                                                             |
| App settings, updater, package assets and templates                           | Arch packaging                                                              |
| Launcher                                                                      | Arch packaging and shell syntax                                             |
| Audio adapters or patches                                                     | Arch packaging and application audio                                        |
| Krisp patcher                                                                 | Arch packaging and native baselines                                         |
| Camera support                                                                | Arch packaging and camera binary compilation                                |
| Capture producers, hooks, protocols and their build scripts                   | Arch packaging, native baselines and independent capture bundle compilation |
| Compiler cache                                                                | Real compiler cache check                                                   |
| Native/audio test fixtures                                                    | Their corresponding integration suite                                       |
| Source/client locks, shared dependencies, selector/workflow, unfamiliar paths | Full checks                                                                 |

Mixed changes use the union of their scopes. Keep the rules conservative: a new
unlisted path runs everything until its consumers have been identified. Changing
the selector or `check.yml` itself also runs everything. No check here recompiles
Electron; compilation remains a separate budgeted workflow.

Only `agents/skills/*/SKILL.md` is classified as skill documentation. Executable
helpers and other new skill resources keep full checks until explicitly scoped.

The **Checks complete** job requires every selected check to pass. A manual
Checks dispatch runs all checks. To inspect a commit range locally:

```sh
uv run python scripts/ci_scope.py --base BEFORE_SHA --head AFTER_SHA
```

## Blacksmith compilation

Reuse a verified runtime first. New attempts require source preflight, fresh
organization-wide usage and a budget reservation in `ci-state`. Missing/stale
usage or credentials block allocation. The allowance is 3,000 x64 2-vCPU minutes
per month; existing reservations and other repositories also consume it.

The 16-vCPU job has a 120-minute limit, including 15 minutes for checkpoints and
uploads. Routine new Electron versions wait 96 hours; verified security fixes
and bounded retries follow [discovery policy](discovery.md#electron-updates).
Healthy or already successful builds of the same identity are not restarted.

For an authorized new attempt, dispatch rather than rerun an old compiler job:

```sh
gh workflow run build.yml --ref main -f target=prerelease \
  --repo discord-patched-electron/discord-patched-electron
```

Preserve retained local workspace paths and objects. Inspect an existing compiler
before changing limits; never run a second Ninja in its directory. Detailed usage
accounting, local-runner commands and credential scopes are in the
[CI reference](ci-reference.md). Only change a retained runner for a current task.
