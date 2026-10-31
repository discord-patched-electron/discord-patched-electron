# OpenAsar settings entry and diagnostics

The renderer adapter replaces the pinned upstream settings injection at runtime;
the pinned source remains unchanged. **DPE Settings** is a native Discord settings
layout entry immediately above Log Out, using Discord's settings cog and default
row variant. It opens the existing configuration window. Both this entry and the
audio dialog use the [shared Discord UI integration](discord-ui.md).

The Troubleshooting tab opens sandboxed windows in the running Electron process,
using its default session. The settings preload exposes only an allowlisted page
name through main-frame-scoped IPC. Repeat requests focus the existing window;
load failures appear in the settings panel. GPU, media-internals, webrtc-internals,
histograms, process-internals and network-errors were verified on Electron
46.0.0-alpha.1. Chrome-only webrtc-logs, net-export, version and chrome-urls failed
to load; tracing loaded with a renderer error, so none are offered.

## Validation (31 October 2026)

- `bun run lint` and `bun run test`.
- `scripts/check_launch_settings.py` reused the retained Electron runtime with a
  fresh profile under Xvfb. Report: `out/dpe-openasar-settings-check-3/settings.json`.
- All six pages loaded; window reuse and unknown-page rejection passed.
- Native layout and shared runtime tests supersede the earlier DOM fixtures; see
  [shared UI validation](discord-ui.md#validation-31-october-2026).
- Existing settings/flags checks passed, including keyboard tabs, 320px layout,
  RTL, 200% zoom, persistence and recovery.

No logged-in Discord session was used. The native layout assertions do not establish a
visual match against a live account’s current sidebar.
