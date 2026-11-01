# Desktop settings refinements, 1 November 2026

General's new startup, tray and notification switches and Flags' three mouse
options now include short descriptions using the shared Switch/flag-row hint.
Appearance's Window section remains description-free. Disabled window-control
rows keep their indentation, and Discord's existing custom controls preserve
their bounds when hidden. The CSS editor has a 480px minimum height, grows when
space is available and remains within the scrolling page.

The flags next-launch notice shares the version footer's typography and inline
padding, including its reserved trailing space. Undo inherits normal text weight.
Successful autosaves no longer add a message below the flag form; necessary
progress remains screen-reader-only, and errors retain their visible alert.
`AGENTS.md` now says not to add confirmation text merely to show that a requested
change was made.

## Mouse dependency

Middle-click autoscroll forces `middleClickPasteAllowed=false` in the resolved
launch arguments after all saved, manual and command-line layers. The editor
retains the independent paste preference and displays its switch checked and
disabled while autoscroll is active. Disabling autoscroll restores that preference.
Explicit Blink feature aliases and disabling overrides follow Chromium's
precedence; recovery still omits custom flags. This uses the existing native
paste fix, without mouse-event interception.

## WebRTC policy removal

The WebRTC IP handling setting was removed from the UI and native adapter.
Saved old keys are ignored by normalization. Equibop's
[picker](https://github.com/Equicord/Equibop/blob/57406f627221bc6054deb9834c18fa9cd914d96a/src/renderer/components/settings/WebRTCIPHandlingPolicyPicker.tsx)
recommends it for VPN/Tailscale voice problems, and its
[startup](https://github.com/Equicord/Equibop/blob/57406f627221bc6054deb9834c18fa9cd914d96a/src/main/startup.ts)
applies Electron's WebContents policy. That affects its browser voice path.
DPE retains Discord's separate `discord_voice` native connection; this policy
cannot select that module's network interface or fix its Tailscale routing.
Keeping a control for unrelated browser connections would not meet the user's
reason for requesting it. The native mouse regression fixture still invokes
the Electron API to test renderer-preference refreshes; that is test coverage,
not a user preference.

## Window controls

The desktop controller observes Electron's existing `browser-window-created`
lifecycle instead of replacing constructors/prototypes. Explicit main, popout
and DPE factories supply their own metadata and preserve unrelated windows'
size constraints. DPE's presentation bridge receives effective control settings
on first load and on saves. It hides the entire custom titlebar area when none
of its available buttons are enabled, and retains its existing Escape behavior.
Native titlebar uses Electron's frame option and suppresses DPE custom chrome.

`patches/ports/46/window-controls.patch` supplies Linux `setWindowControls` and
`getWindowControls` on Electron's existing BaseWindow API. A per-window visibility
mask filters Chromium's native frame layout before button placement; it does not
change minimize/maximize/close capabilities. Restricted native-framed auxiliary
windows use Chromium's existing GTK client frame, since server decorations have
no portable per-button visibility protocol. Restoring all controls restores the
original decoration mode. Frameless windows remain frameless, and selecting
native titlebar restores normal OS buttons.

Native frame changes preserve content bounds. Minimized windows defer changing
their applied frame mode until the Widget's native restore acknowledgement;
the requested mask remains available immediately. This avoids corrupting the
restore bounds during a save while minimized or immediately after calling
restore/show. Maximized windows still apply changes live. No mouse/keyboard
listeners, global Electron prototype replacements or HTML titlebars are involved.

The verified runtime artifact is
`/mnt/storage/dpe-native-controls-20261101/artifact-v2`, retaining the earlier
native paste fix. Its runtime key is
`dfe843643db999ec0d6f392412deeb7ce73ddc0118f879e5601089de61afbdde`.
Its native source tree is `f67eae412bc4ed3314db1880cc6e63706c29d260`.
The source/patch assembly was independently reapplied and matched that tree.

Focused and native test evidence lives under
`/mnt/storage/dpe-settings-refinements-20261101/`.

## Verification and UI review

`bun run lint` passes. `bun run test` passes all 509 Python and 246 JavaScript
tests. The real Electron settings harness passes 39 check groups against the
final native runtime (`settings-final/settings.json`), including the shared
sidebar typography/padding, disabled indentation, 480px CSS minimum, autosave
feedback, dependent mouse switches and individual/all DPE titlebar controls.
Existing spelling, tray presets, keyboard, narrow-window, RTL, zoom and token
checks remain passing.

The better-ui review used native settings screenshots and the shared Discord
replica components. No blocking visual findings remain in the inspected states.
The changes preserve semantic tokens and existing control geometry; the native
GTK frame is OS window chrome, not a new HTML settings design.

Native X11 checks (`native-v2-0/result.json` and `native-v2-1/result.json`) pass
individual/all button visibility, newly created auxiliary windows, native-titlebar
restoration, minimized/maximized updates and unchanged restoration bounds.
Actual Openbox Alt+F4, Alt+F9 and Alt+F10 shortcuts remain functional. Nested
niri/Wayland checks (`native-wayland-frames/result.json`) also pass live hiding,
new windows, restoration and maximized updates. That isolated GTK theme exposes
only Close by default; individual Minimize/Maximize visuals were checked on X11.
Native mouse regressions pass default paste, blocked paste and autoscroll modes,
including renderer-preference refreshes, Ctrl+V, middle-click links and canvas/
iframe auxiliary clicks.

The final packaged build is `app-ready/`. Its fresh-profile launch check passes
onboarding, the real Discord login page and two popout handshakes
(`launch-ready/launch-report.json`). The packaged executable matches the native
settings runtime SHA-256
`5a36b7c2d18848a32442b7a7b2b28ec7f9361c55a81292a7f757f70424cf99e8`.
This is a local test build, not full release/media qualification; no signed-in
calls or activities were exercised and no host desktop preferences were changed.

Run `/mnt/storage/dpe-settings-refinements-20261101/test.sh` for the prepared
build. It uses the earlier isolated manual profile and pins the bundled native
runtime. `TESTING.txt` beside it describes the changes and profile override.
