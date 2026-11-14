# Working on this repository

Start with [CONTRIBUTING.md](CONTRIBUTING.md). Maintained guides and retained
evidence are indexed in [docs/agent/README.md](docs/agent/README.md).
Load only references relevant to the task.

## Repository skills

Reusable workflows live in `agents/skills/`. For a matching task, read the
relevant `SKILL.md` before implementing changes. Load its linked references only
as needed. Paths in commands assume the repository root.

| Skill                                                               | Use for                                                                              |
| ------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| [dpe-upstream-review](agents/skills/dpe-upstream-review/SKILL.md)   | Discovery/release failures and complete Discord/Electron update diffs                |
| [dpe-native-patches](agents/skills/dpe-native-patches/SKILL.md)     | Binary patch chains, ABI scopes, compiled hooks and Electron source ports            |
| [dpe-updater](agents/skills/dpe-updater/SKILL.md)                   | Bootstrap, update activation/recovery, native updater APIs and mod preservation      |
| [dpe-settings-ui](agents/skills/dpe-settings-ui/SKILL.md)           | Preact settings, shared controls, presentation and Discord page adapters             |
| [dpe-runtime-checks](agents/skills/dpe-runtime-checks/SKILL.md)     | Isolated desktop, native media, audio and camera validation                          |
| [dpe-capture-checks](agents/skills/dpe-capture-checks/SKILL.md)     | Screenshare codecs, capture backends, lifecycle and matched benchmarks               |
| [dpe-dev-environments](agents/skills/dpe-dev-environments/SKILL.md) | Managed dev versions, independent profiles, helper watch/restart and storage cleanup |
| [dpe-build-release](agents/skills/dpe-build-release/SKILL.md)       | Runtime reuse, local packages, qualification and budgeted compilation                |

Keep reusable instructions in these skills and maintained guides. Skills do not
grant permission to publish, approve releases or repeat historical machine operations.

## Agent documentation

Maintain the subsystem references indexed in `docs/agent/README.md` in place.
Do not create dated task reports, review diaries, handoffs, history indexes or
archive copies unless the user explicitly requests a separate document. Routine
fix summaries and test counts belong in the PR/final response.

Keep raw logs, full upstream inventories/hunk dispositions and investigation
scratch work under `out/`. Put only durable contracts, reproducible benchmark
results, qualification input identities/coverage and unresolved limits in the
relevant existing reference; replace superseded results instead of appending
retry chronology. This changes documentation storage, not review/test coverage or
user-facing explanations. Use the
[maintenance skill](agents/skills/dpe-agent-docs/SKILL.md) for consolidation.

## Discord replica UI

- DPE surfaces must look like Discord's current settings and dialogs. Reuse the
  local replica components; do not introduce generic browser controls or a new
  visual design for a feature. Keep new settings in the separate DPE window.
- Compose settings with Preact in `integration/updater/ui/`. Start with
  `primitives.jsx` and `settings-controls.js`: use the shared Switch, Button,
  Icon, disclosure and upgraded select controls. Language controls use the same
  switch as other settings. Reuse the existing search-field treatment.
- Share styling through `integration/discord-ui/components.css` and semantic
  tokens. Preserve live Discord colors, typography, spacing, radii, control
  heights, root font size and zoom from the presentation bridge. Use measured,
  bounded fallbacks when upstream tokens are unavailable, not hardcoded copies
  of a screenshot's scaled pixel sizes.
- Route icons through the shared icon component/resolver. Preserve the native
  drawing, viewBox and opacity; use the existing fallback set when needed.
  Do not substitute emoji, text glyphs or unrelated icon libraries.
- Extend a shared component when a new state is needed instead of copying its
  markup and styling into a page. Controlled inputs must reflect new values,
  disabled states and callbacks after rerenders. Keep focus, selection and scroll
  stable during saves, filtering and status refreshes.
- Implement requested changes without adding unnecessary confirmation text,
  status banners or subtitles merely to show that work was done. Add copy when
  it explains a setting, a real error, or an action the user needs to take.
- Check enabled, disabled, selected, loading, empty and error states; keyboard
  use; narrow windows, zoom and long labels; reduced motion; and theme tokens.
  Run the real settings harness for changes to settings components and layout.
  Do not claim exact visual parity from unit tests alone.

Dependent controls belong directly beneath the option that enables/disables them,
using the shared indented settings group. Keep their values while disabled.
Select rows include a description when it explains the choice; rows without one
vertically center the title against the control. Keep this behavior in shared
components, including Display flags and PiP settings.

See [shared UI architecture](docs/agent/discord-ui.md) for rationale, component
provenance, measurements and validation. These conventions apply to all future UI
changes, including new tabs and settings.

## Upstream update reviews

For Discord/Electron updates and discovery failures, follow the complete
[upstream update review workflow](docs/agent/upstream-update-review.md). Compare
immutable last-qualified and candidate snapshots, inventory every changed
payload/ASAR/native module, and give every diff hunk an explicit disposition.
Fix and validate every affected integration path; do not stop at the first
failure, merely extend a native hash allowlist, or treat old-lock tests as proof
that the candidate works. Record review coverage and remaining limits in agent
docs before declaring completion.

## Working rules

- Use `uv run` for Python. Run `bun run lint` before finishing, and `bun run test`
  for code, configuration or workflow changes. Documentation-only edits need lint
  and a review of changed links/instructions. Scope integration checks to affected
  components; see [CI check selection](docs/agent/ci-setup.md#check-selection).
- Choose new tests by a concrete failure mode; extend existing behavior coverage
  before adding fixtures or workflows. Do not add source-spelling assertions,
  duplicate syntax checks or tests for routine reversible copy/style changes.
  Preserve ABI, update recovery, signing and real end-to-end checks. Follow the
  [test design policy](docs/agent/ci-setup.md#test-design-and-coverage) and workflow
  ownership before expanding CI; a red run alone is not a reason to remove a gate.
- Preserve pinned upstream files and patches; don't format them.
- Reuse verified runtimes for packaging. New CI compilations use the budgeted
  Blacksmith workflow. Don't restart a healthy compiler or discard retained local objects.
- Test Discord with fresh profiles. No unattended account login, messages or calls.
- Never save or upload webcam images/video, or screenshot the desktop during
  physical camera tests.
- Treat old agent notes as historical evidence; consult current code and locks
  before making changes. Don't restart old monitors or hibernate the PC without
  a current instruction to do so.

## Development branch synchronization

Before starting work on `dev` and before pushing it, fetch `origin` and merge
`origin/main` without rewriting development commits. Preserve uncommitted work
before merging and resolve conflicts explicitly. Never merge `ci-state` into
`dev` or `main`. `.github/workflows/sync-dev.yml` automates main-to-dev merges
once installed on the default branch; its conflicts still require a maintainer.
