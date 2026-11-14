---
name: dpe-updater
description: Change or debug DPE bootstrap, application/client/runtime updates, native updater bridges, activation recovery, or Vencord and Equicord preservation.
---

# Work on the DPE updater

Read the relevant sections of [the updater design](../../../docs/agent/client-updater.md),
including mod preservation when affected. Verify
these descriptions against current code; dated examples are evidence for their
run, not current pins. Run commands from the repository root.

## Trace the affected layer

| Layer                                               | Starting points                                                                                  |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| Installed trust boundary and application activation | `integration/bootstrap/loader.py`, `integration/updater/application.py`, `application-ready.cjs` |
| Client pins, private preparation and module caches  | `integration/updater/client.py`, `scripts/patch_app_audio.py`                                    |
| Runtime identity and cached generations             | `integration/updater/runtime.py`, `scripts/runtime_fingerprints.py`                              |
| Writable installation and crash recovery            | `integration/updater/installation.py`, `client_mods.py`                                          |
| Desktop/native API and notifications                | `integration/updater/entry.cjs`, `patches.cjs`, `module-updater.cjs`, `runner.cjs`               |

Preserve the separation between the installed loader, application payload,
patched runtime and Discord modules. A signed release alone does not authorize
automatic updates: exact pins and the matching manual approval record matter.
Same-version module changes also need approval. Do not query Discord's live
latest manifest as a fallback around this policy.

## Preserve activation and running instances

Preparation must leave running code and resolved module paths unchanged. Retain
shared in-flight work, update events and desktop menu/tray/timer/Quickstart
notification paths. Trace both legacy and native updater consumers when APIs
change; use approved installed modules for native lookup/install requests.

Preserve file-index verification, safe archive extraction, compatibility keys,
locks, activation journals, SQLite transactions and started/ready trial markers.
Distinguish interrupted setup, an unconfirmed client trial, concurrent launches
and PID reuse. Offline recovery must use authorized verified caches; failures
must retain the working generation and diagnostic evidence.

Keep official mod loaders and their original payload paths. The active profile
installation is authoritative after takeover. Uninstalling or switching a mod
must not resurrect it from an older app or package backup. Mod payloads and
settings remain outside the client rollback transaction so a mod self-update
survives failed activation.

## Validate changed contracts

Use the corresponding `tests/test_application_updates.py`, `test_client_updater.py`,
`test_runtime_updater.py`, `test_installation.py`, `test_client_mods.py` and
`tests/updater/*.test.cjs` cases. Add meaningful failure/recovery regressions for
the behavior changed. Check exact current filenames before selecting tests.

For desktop behavior run `scripts/check_desktop_launch.py` with relaunch; for
mods use `scripts/check_client_mods.py` with verified installer inputs. Use a
private app copy and fresh profiles. Cover preparation, activation, offline reuse
and real exposed APIs where affected; a mocked bridge cannot prove startup works.
An upstream update also needs the [whole-diff review](../dpe-upstream-review/SKILL.md).
