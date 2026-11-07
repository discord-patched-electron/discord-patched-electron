# Maintainer guide

Start with [contributor setup](../../CONTRIBUTING.md). For app installation and
settings, use the [main README](../../README.md). Check current code and locks
before reusing versions or commands from a dated report.

## Common tasks

| Task                                               | Guide                                                                     |
| -------------------------------------------------- | ------------------------------------------------------------------------- |
| Run dev versions, switch profiles or watch helpers | [Development manager](../development.md)                                  |
| Package, qualify or approve a release              | [Release packaging](releases.md)                                          |
| Investigate discovery or an upstream update        | [Whole-diff review](upstream-update-review.md), [discovery](discovery.md) |
| Choose checks or compile Electron                  | [CI runners](ci-setup.md), [runtime validation](runtime-validation.md)    |
| Change settings or native integrations             | [Repository skills](../../AGENTS.md#repository-skills)                    |

## Implementation references

- **Latest client review:** [Canary 1.0.2472 dev validation](client-2472-compatibility.md).
- **Updates:** [client/runtime updater](client-updater.md), [mod preservation](client-mods.md).
- **Builds:** [source ports](source-ports.md), [cache reuse](build-caching.md),
  [channel switches](channels.md).
- **Desktop:** [shared UI](discord-ui.md), [application audio](wayland-audio.md),
  [capture review](screenshare-review-20261105.md).
- **Operations:** [release internals](release-reference.md),
  [runner and credential details](ci-reference.md).

## Investigations and evidence

The [investigation index](history.md) preserves dated reports, decisions and test
limits. Store new investigation notes here and link them from that index.
User guides should explain setup and use; keep disassembly, experiments and
individual test runs in these maintainer references.
