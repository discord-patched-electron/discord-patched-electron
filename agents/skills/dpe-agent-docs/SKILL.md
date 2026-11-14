---
name: dpe-agent-docs
description: Consolidate DPE maintainer references without recreating dated task reports, preserving useful measurements, compatibility contracts and qualification limits.
---

# Maintain DPE agent references

Use the existing subsystem reference in [the index](../../../docs/agent/README.md).
Read only its relevant sections. Follow the
[documentation policy](../../../AGENTS.md#agent-documentation).

- Update contracts/procedures in place. Keep benchmarks with exact inputs,
  method, results, raw artifact path and limitations. Replace superseded results;
  do not append retry chronology, test counts or completion narratives.
- Keep full upstream inventories/hunk dispositions, raw logs and temporary
  investigation/handoff material under `out/`. Summarize qualification identities,
  coverage and unresolved limits in the existing upstream/subsystem reference.
- Do not add dated reports, archives, history indexes or new reference files unless
  the user explicitly requests a separate document. Git preserves removed history.
- When consolidating, inspect content and incoming references, preserve useful
  contracts/measurements, and repair links, anchors and textual citations. Preserve
  unrelated working-tree edits and raw evidence.

Run lint and review links/instructions. Documentation consolidation needs no
runtime benchmark or Electron build and does not weaken required review coverage.
