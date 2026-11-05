---
name: dpe-settings-ui
description: Implement or review DPE settings, dialogs, picker controls and Discord presentation bridges using the local Preact replica components and real settings harness.
---

# Change DPE settings and shared UI

Follow the root [UI conventions](../../../AGENTS.md#discord-replica-ui) and
[shared UI architecture](../../../docs/agent/discord-ui.md). Keep DPE settings in
the separate window. Run commands from the repository root.

## Reuse the presentation system

Start with `integration/updater/ui/primitives.jsx`,
`integration/updater/settings-controls.js` and the existing page components.
Extend shared controls for new states instead of copying markup. Use the shared
Switch, Button, Icon, disclosure, select and search treatments, including for
language controls.

Share CSS through `integration/discord-ui/components.css` and semantic tokens.
Preserve presentation-bridge colors, typography, spacing, radii, root font size
and zoom. Fallbacks must be measured and bounded. Route icons through the shared
resolver, retaining their drawing, viewBox and opacity. Avoid screenshot-scaled
pixel copies, emoji substitutes and extra banners or copy that explain no user
action.

Keep controlled values, callbacks and disabled states current after rerenders.
Keep focus, selection and scroll stable during saving, filtering and status
refreshes. Settings IPC remains restricted to the trusted local main frame;
presentation data does not grant page-world Node or general IPC access.

For Discord page adapters, retain the shared webpack discovery/serialization
runtime, ambiguity handling, immutable layout lists and supported mod wrappers.
Do not pin module IDs or invoke settings controllers just to discover exports.
Read the architecture's Equicord section when touching factory hooks.

## Validate the real controls

Use the relevant `tests/updater/` and `tests/discord-ui/` regressions and run the
real settings harness against a verified runtime and a fresh output:

```sh
uv run python scripts/check_launch_settings.py /path/to/runtime/electron \
  --output out/settings-check
```

The harness rebuilds the local Preact bundles. Optional `--media-cache` verifies
the pinned fonts from a local cache. `DPE_SETTINGS_SCOPE` supports focused
`screensharing`, `popout` and `media-controls` checks; choose one only when it
covers the change. Inspect current harness options rather than inventing a scope.

Check enabled, disabled, selected, loading, empty and error states, keyboard use,
narrow windows, zoom, long labels, reduced motion and theme tokens. Inspect the
rendered result for layout work. Unit tests alone do not prove visual parity;
report compositor/window-size constraints instead of calling a failed harness
a pass. Native Discord entry/picker changes also require the affected isolated
audio/native settings probe described in the architecture guide.
