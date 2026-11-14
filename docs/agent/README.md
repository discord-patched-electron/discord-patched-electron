# Maintainer references

Start with [CONTRIBUTING](../../CONTRIBUTING.md) and the matching
[repository skill](../../AGENTS.md#repository-skills). Read the relevant section,
not the whole directory. Current code, locks and manifests define shipped behavior;
benchmark snapshots below describe only their recorded inputs.

| Task                                                  | Reference                                                         |
| ----------------------------------------------------- | ----------------------------------------------------------------- |
| Select CI checks, test design, compiler budget        | [CI](ci-setup.md)                                                 |
| Package, reuse runtimes, port Electron, sign releases | [Builds and releases](releases.md)                                |
| Review Discord/Electron updates and discovery         | [Upstream review](upstream-update-review.md)                      |
| Bootstrap, activation, recovery, mods                 | [Updater](client-updater.md)                                      |
| Managed dev versions, helper reload, storage          | [Development](dev-environments.md), [commands](../development.md) |
| Preact settings, Discord adapters, presentation       | [Shared UI](discord-ui.md)                                        |
| Launcher, flags, links, popouts, spelling, tray       | [Desktop](desktop.md)                                             |
| Application-audio identity, routing, cleanup          | [Audio](audio.md)                                                 |
| Native patch scopes, camera, codecs, AV1 framing      | [Native media](native-media.md)                                   |
| Capture ownership, backends, permissions, picker      | [Capture](capture.md)                                             |
| Reproduce measurements and interpret results          | [Benchmarks](benchmarks.md)                                       |
| Run isolated desktop/native/media checks              | [Validation](runtime-validation.md)                               |

Maintain these subsystem references in place. Do not add dated task reports,
handoffs or another history/index. Keep raw logs and investigation scratch work
under `out/`; record only durable conclusions, reproducible measurements and open
limits in the relevant section. See [documentation policy](../../AGENTS.md#agent-documentation).
