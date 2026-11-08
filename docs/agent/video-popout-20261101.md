# Video and screenshare popouts

Discord's public Canary web bundle currently opens both whole-call and individual
camera/screenshare windows through `/popout`. Their keys are
`DISCORD_CHANNEL_CALL_POPOUT` and `DISCORD_CALL_TILE_POPOUT_<channel>_<participant>`.
Both default to 854×480 and restore saved geometry. They use the existing
[opener/sandbox fix](debug-popout.md); they do not use Document Picture-in-Picture.

The desktop adapter now passes the main window into Discord's origin-checked
popout constructor. Only video keys get non-modal transient/dialog hints,
background rendering and initial geometry bounded to a display's work area.
Valid saved sizes and monitor positions are retained. Screen coordinates are
already logical pixels, so no extra DPI multiplication is applied. Wayland
compositors retain control of placement.

Electron 46's Linux `SetParentWindow` sets `WM_TRANSIENT_FOR` on X11, but does
not send a Wayland parent. Its `type` option is also X11-only here. A real niri
fixture therefore still tiled the window at 616×768 with just those hints.
The adapter also supplies equal initial minimum/maximum dimensions, then restores
resizing and normal constraints on the first native focus event, after mapping.
Releasing them at `ready-to-show` was too early and reproduced tiling. A WM rule
that suppresses focus leaves the startup dimensions fixed until the user focuses
the popout. Explicit compositor rules can override default floating behavior.

The minimum-size preference is applied after mapping, and temporary startup
constraints are never retained as the normal minimum when settings are saved.
Debug, activity, soundboard and overlay windows keep their existing behavior.
No compositor configuration is edited and no new Electron build is required.

Upstream references: [Electron native window implementation](https://github.com/electron/electron/blob/main/shell/browser/native_window_views.cc)
and [niri floating rules](https://github.com/niri-wm/niri/blob/main/docs/wiki/Floating-Windows.md).

## Validation

`scripts/check_popout.py` copies a supplied runtime, creates fresh profile/HOME/XDG
paths, and starts private Xvfb and niri instances. It never connects to the user's
display, Discord profile or audio server. The harness loads the actual patched
popout module from an extracted desktop-core bundle. Its only video is an animated
canvas stream; no camera, screen capture, account, call or screenshots are used.

```sh
uv run python scripts/check_popout.py /path/to/runtime/electron \
  --core /path/to/extracted-core/bundle.js --output out/popout-check
```

Add `--no-minimum-size` to exercise the disabled preference. Requires Xvfb, niri
and the normal runtime dependencies. The report includes runtime/core hashes.

The retained Electron 46.0.0-alpha.1 runtime passed with desktop-core 1.0.2422
(default constraints) and the locked 1.0.2357 core (minimum-size preference off):

- Both video keys float according to niri IPC, initially at 854×480.
- The opener handshake delivers a synthetic MediaStream into the child and
  receives video frames. Playback continues after focus returns to the parent.
- Resizing to 640×360 works, maximum constraints are released, and closing then
  reopening the same tile key works. The diagnostic window still renders.
- Page Node globals remain unavailable. The existing origin allowlist and
  sandbox compatibility transform are retained.

Evidence: `out/pip-investigation/final-default/result/report.json` and
`out/pip-investigation/final-no-minimum/result/report.json`. A further
`out/pip-investigation/final-constraints/result/report.json` run checks live minimum-size
preference changes without enlarging the resized popout. Unit coverage includes
HiDPI logical dimensions, secondary/disconnected displays, oversized/tiny windows,
non-video exclusions and constraint restoration across preference changes.

Live Discord calls, X11 tiling managers and other Wayland compositors were not
validated. The running Discord session and installed application were not modified.

`bun run lint` and `bun run test` passed (548 Python and 259 JavaScript tests).

## Follow-up: no window opens

The user clarified that Discord's built-in call/screenshare **Pop Out** button
opens no window; the mod's separate video PiP button was not tested. The floating
and geometry changes above do not establish a fix for that opening failure.
Read-only inspection of the application generation recorded for the running
process confirmed that it already includes the opener/sandbox fix. No connection
to the running renderer, account action or restart was performed.

An additional isolated synthetic-video probe successfully opened Chromium's
`video.requestPictureInPicture()` on the retained runtime. That is a different
API and does not reproduce or resolve the reported call-popout failure. The
built-in failure remains unresolved pending evidence from the failing button
action, such as its renderer console error. Do not report it fixed based solely
on the synthetic window tests above.

The reported request uses `DISCORD_CHANNEL_CALL_POPOUT` and
`menubar=no,toolbar=no,location=no,directories=no,width=854,height=480,`.
The lifecycle fixture and login-page launch check now use these real video keys
and features. A copy of the installed runtime (executable SHA-256
`6d0e3db61278f7be23afbb0129361d6fe4d30c9547e7fe5d1248740b9af57cb5`)
passed the isolated lifecycle fixture. The full-app test stopped at the updater;
it did not establish a successful call-popout launch. All desktop tests were
stopped after the user reported severe desktop lag. Do not resume those harnesses
without a new instruction that authorizes that workload.

The later renderer check reported no registered child, but the user clarified
that the screenshare may already have ended. That result is inconclusive for the
failure while a screenshare is active.

## Manual candidate

Code inspection found a separate concrete defect in the existing-window branch:
it called `focus()` without showing/restoring the window, then cleared its
always-on-top state. Video popouts now restore if minimized, show, and focus,
without changing the pin. Other window types retain their previous behavior.
Unit tests cover both hidden and minimized windows and pin preservation.

The private candidate is `out/pip-manual-test/launch-test.sh`. It contains the
installed application baseline with only the PiP adapter/patch changes, the
checksum-verified installed Electron runtime and module caches, and a fresh
unmodded profile. No account files or saved conversations were copied. Python
offline preparation completed; the candidate has not been launched automatically.
Runtime/application updates are disabled for this manual candidate.

The launcher enables `DPE_POPOUT_TRACE_FILE` at
`out/pip-manual-test/pip-trace.jsonl`. Opt-in tracing records requested, allowed,
created, ready, focused, closed and failed-load stages, plus reuse of an existing
window. It records call/tile kinds rather than window keys, URLs or channel/user
IDs. Logging failures cannot prevent opening a window. The next manual report
should include this trace while a call/screenshare is still active.

Lint and the unit suite passed (561 Python, 267 JavaScript), limited to one CPU at
low priority. Both desktop-core layouts accept the staged transforms. These
checks do not establish that the user's original opening failure is fixed.

## Configurable PiP candidate, 2 November

`Appearance → Video popouts` now contains three enabled-by-default switches:
`popoutFloating`, `popoutHideTitlebar`, and `popoutHideMembers`. Floating changes
apply to newly opened windows. Turning it off omits both transient/dialog hints
and the fixed initial size; it does not force an existing window into tiling.
Global Discord window controls Off always wins. Native titlebar keeps its existing
precedence. The floating popout can otherwise hide its own Discord titlebar.

Inspection of the current public web bundle found that the legacy Windows bar
includes a decorative Discord wordmark and supports optional children. When all
controls are hidden, collapse the complete legacy bar only if its direct children
are the known wordmark/window buttons. Preserve bars with additional children and
the modern toolbar's Help/Inbox content. This applies to legacy popouts including
debug popouts; floating and media sizing remain restricted to call/tile windows.

The shared Discord UI runtime discovers ChannelRTCStore and the native
`selectParticipant`/`toggleParticipants` actions from their source signatures,
without hardcoded module IDs or translated labels. After an explicit stream
selection, collapse participants only if that stream remains selected. Camera
and activity selection are unchanged. Manual Show Members stays usable; there
is no recurring collapse loop. Disabling the switch takes effect live.

Initial geometry uses a bounded fraction of the logical display work area. A
new video popout then gets one metadata-based adjustment: choose its dominant
visible video, preserve its aspect, account for native/renderer chrome and zoom,
and convert media pixels using display scale. A ten-second event-driven discovery
deadline retains the fallback for grids or absent media. No frames are read or
captured. A resize/move before metadata arrives cancels adjustment, and valid
saved geometry bypasses it. Custom aspect ratios can lower the appropriate
minimum axis instead of distorting portrait or ultrawide content.

Request bottom-right placement through Electron for conventional desktops. Native
Wayland positioning remains compositor-owned. On niri, correlate first focus with
our PID and title, retain the verified window ID, resolve its monitor through
workspace/output IPC, and move only that ID if it remains our floating window.
Never issue a command targeting whichever window happens to be focused later.
Other Wayland compositors can ignore the position request. See the upstream
[floating-window behavior](https://github.com/niri-wm/niri/blob/main/docs/wiki/Floating-Windows.md)
and [Electron coordinate documentation](https://www.electronjs.org/docs/latest/api/screen).

The real settings harness has a focused `DPE_SETTINGS_SCOPE=popout` mode. With
Ozone headless and hardware acceleration disabled, it passed defaults,
dependencies, save/rerender persistence, keyboard activation, a narrow 150% zoom
layout, legacy-content preservation, and dominant-video metadata discovery.
Evidence: `out/pip-settings-headless-20261102-4/settings.json`. This uses only
synthetic DOM/media dimensions, no Discord login or desktop screenshot. The
broader compositor/call tests were not resumed. Commands were restricted to one
CPU at low scheduling/I/O priority. Unit coverage also checks cancellation,
global precedence, opt-out, aspect ratios, and niri ID/output isolation.

The updated manual build is `out/pip-manual-test-v2/launch-test.sh`, prepared with
the same verified runtime and fresh unmodded account profile. It excludes the
concurrent webcam changes, preserves the first manual build, and has not been
launched automatically. Test Pop Out while the screenshare is still active;
`pip-trace.jsonl` remains the useful diagnostic if no child appears. The original
live opening failure and placement on the user's actual compositor remain for
manual verification.

Final validation passed: `bun run lint`, 561 Python tests and 274 JavaScript tests.
The staged private adapter patches parse against both retained desktop-core
layouts, its bundle contains all three PiP controls, and its runtime/resources
paths resolve entirely inside the new manual build. Changed artifact hashes are
recorded in `out/pip-manual-test-v2/build.json`.
