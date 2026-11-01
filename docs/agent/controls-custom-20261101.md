# Collapsed window controls and Custom tray state, 1 November 2026

Disabled Discord window buttons now use `display:none`, collapsing their slots.
When all three are off (including individual switches or native-titlebar mode),
the complete button container and its divider disappear. The current Discord
top bar restores its own `--space-12` trailing inset for Help/Inbox. This is the
upstream default that Discord's Linux/Windows rule overrides to zero when native
style controls occupy the edge; it is not a replacement spacer. Logical padding
supports RTL and responds to live spacing-token changes. Selectors remain scoped
to the existing window-control containers; activity and other toolbar buttons
are unaffected. DPE's individual buttons also collapse, and its entire custom
titlebar still disappears when all are disabled.

The public Canary CSS was checked in a fresh, logged-out source probe. Its rules
and current source are retained in
`/mnt/storage/dpe-controls-custom-20261101/source-probe/`. Native screenshots and
geometry checks live under `desktop/` beside it. They verify individual and
complete removal, the 12px inset, a changed 20px spacing token, RTL and exact
restoration of upstream geometry, plus existing legacy and auxiliary controls.
This supersedes the preserved hidden-button space in the earlier
[settings refinements](settings-refinements-20261101.md).

The tray preset selector displays **Custom** when a custom image or per-state
preset differs from the baseline. It returns to the baseline name when those
differences are removed. Custom is a derived placeholder in the shared Select,
not a selectable preset or a persisted setting; normal preset choices remain
keyboard accessible. The stored baseline and individual overrides are retained.

The local test build and command are in
`/mnt/storage/dpe-controls-custom-20261101/`. The build reuses the verified native
v2 runtime; no C++ changes or recompilation were needed.

## UI review and validation

| Severity | Location                                                                       | Before                                                | After                                                                                | Why                                                               |
| -------- | ------------------------------------------------------------------------------ | ----------------------------------------------------- | ------------------------------------------------------------------------------------ | ----------------------------------------------------------------- |
| Medium   | `integration/desktop/preferences.cjs`, `integration/updater/ui/primitives.jsx` | Hidden buttons retained empty slots.                  | Buttons collapse; the top bar restores its upstream trailing inset when all are off. | Preserve intentional surface spacing without a control-sized gap. |
| Low      | `integration/updater/ui/desktop.jsx`                                           | Mixed icons still displayed the baseline preset name. | The shared selector displays Custom until icons match again.                         | Give the current state an accurate static label.                  |

Lint and 757 tests pass (509 Python, 248 JavaScript). The real settings harness
passes all 41 groups, including collapsed DPE button geometry and Custom for
both imported images and mixed presets, across remounts and resets. Native
desktop tests and screenshots verify the upstream top-bar layout, RTL and token
changes. Settings screenshots retain the existing replica Select appearance.
Source files and the UI bundle match the packaged build.
The fresh-profile packaged launch also passes onboarding, the real login page
and both popout handshakes (`launch/launch-report.json`).

No actionable UI-polish findings remain in the inspected states. An authenticated
Discord session was not used; the top-bar fixture uses the verified public CSS
and structure. Approve for the inspected states.
