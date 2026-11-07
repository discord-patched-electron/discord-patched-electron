---
name: dpe-upstream-review
description: Investigate DPE discovery or release qualification failures and review Discord or Electron updates against the last qualified snapshot, covering every changed payload and affected integration.
---

# Review a DPE upstream update

Read the [required whole-diff workflow](../../../docs/agent/upstream-update-review.md)
before investigating. It defines the inventory, disposition record and candidate
qualification requirements. Run commands from the repository root; keep evidence
under a fresh `out/` directory.

## Establish the actual candidate

- Read the failed job's complete logs, retained reports and exact client lock.
  Compare with the last **qualified** snapshot, which can be newer than the
  bootstrap lock. Record immutable source/state commits and archive hashes.
- Use [discovery and scheduling](../../../docs/agent/discovery.md) to distinguish
  discovery, source preparation, compilation, qualification and approval. A later
  successful poll may simply have found no work.
- Fetch snapshots with `scripts/fetch_client_snapshot.py`; verify downloads before
  inspecting them. Do not execute a downloaded bootstrap to learn its version.

## Review and fix the complete delta

Inventory both directions for every archive and ASAR, including removed files,
preloads, native modules, assets and same-version replacements. Format only
comparison copies of minified text. Give every changed file and text hunk a
disposition with its callers, fix or reason for no DPE impact, and evidence.

Trace main/preload/renderer contracts and all update triggers, module APIs,
supported mods, popouts, permissions and packaging. Distinguish intentionally
blocked telemetry from broken application traffic; do not allow telemetry or
relax CSP merely to silence a console error. Preserve useful upstream fixes.

For native changes, use the
[native patch skill](../dpe-native-patches/SKILL.md). For activation, approval or
mod preservation failures, use the [updater skill](../dpe-updater/SKILL.md).
A startup fix or a larger hash allowlist does not finish the update review.

## Qualify and report

Test the exact candidate with the complete working-tree preparation code and
adapters. Select checks from the affected contracts: login/relaunch, real module
APIs, update notifications, mod lifecycle, native media, capture lifecycle,
settings and package payloads. Reuse a verified runtime unless source changes
actually require compilation.

Run repository lint and tests. Reconcile all inventory entries in an agent note
linked from [the index](../../../docs/agent/README.md), retaining failed attempts
and explicit environment/coverage limits. Qualification does not authorize
publication, approval or unrelated machine actions.
