# Desktop integrations

User-facing setup is in [the launcher guide](../linux-launcher.md).
Current adapters live in `integration/desktop/`; in-memory transforms validate
unique upstream sites before activation. Unsupported/ambiguous layouts fail
closed. Do not edit downloaded client archives or replace global Electron
constructors/prototypes to implement preferences.

## Launcher and flags

Preserve standard Canary desktop/profile identity through bootstrap dialogs,
splash, main window, settings, popouts and autostart. `CHROME_DESKTOP`, icon and
WM_CLASS/app ID must match the installed entry. `DISCORD_USER_DATA_DIR` selects
a parent directory; `DPE_PROFILE_DIR` is an exact override. Tests use fresh
profiles and short temporary paths, never the normal account profile.

The flag resolver merges presets, saved/manual flags and explicit command-line
overrides using Chromium precedence. Recovery omits custom flags. Flags storage
and atomic save/Undo behavior belong to the shared resolver, not page-local
parsers. Preserve unrelated members when editing `--enable-features`,
`--disable-features` and named Blink settings. Dynamic flags must not create a
new Electron build identity. GPU acceleration must be verified in the actual
playback/capture path: a `vainfo` profile or enabled feature name is insufficient.
Do not remove sandboxing or add a blanket GPU workaround for a codec failure.

## Desktop preferences

Validated key/value patches merge into current config; stale page snapshots must
not overwrite the namespace. Persistence failure restores the old config before
live application. A failed native update keeps the saved preference and requests
a restart instead of pretending the save failed. Main/popout/DPE factories supply
window metadata; unrelated windows retain their constraints.

Native titlebar needs a new BrowserWindow. Individual button preferences remain
stored while forced off. Hidden controls collapse; all-hidden mode restores the
native token-based trailing inset, including RTL. Native Linux per-button masks
change frame layout without removing WM capabilities. Minimized windows defer
frame-mode changes until native restore acknowledgement, preserving content and
restore bounds. `window-controls.patch` and `middle-click-paste.patch` need a
matching compiled runtime; JavaScript settings alone cannot supply these APIs.

Autoscroll forces `middleClickPasteAllowed=false` after all flag layers while
retaining the user's independent paste preference. The native patch preserves
this explicit value across renderer-preference refreshes. Never intercept all
middle mouse events or clear the clipboard. Ctrl+V, links, canvas and iframe
auxiliary clicks remain functional. Browser WebRTC IP policy was removed because
it cannot select the separate native voice module's network interface.

Start minimized does not enable OS autostart. Disabling the tray gates close-to-tray
and restores hidden main windows. Mentions-only badges retain the latest numeric
notification count and filter the shared tray accessor before stock/custom icons.
An unread boolean or `-1` sentinel is not a mention count; voice/mute/deafen
priority is preserved. Tray assets and derived Custom state are in
[shared UI](discord-ui.md#shared-settings-behavior).

## Spelling

Electron owns dictionaries and native suggestions. Chromium defers unfinished
caret words until a boundary; an empty result there is not proof of a missing
dictionary. A separate Chromium 158 regression ignored programmatic focus on
prefilled editors; native spellcheck source ports and their runtime tests cover
that case. Do not replace it with a DOM spelling engine.

The Languages action intercepts only Discord's exact spelling help article
`360000877191` from trusted HTTPS Discord documents and opens DPE Spelling.
Manual selections, downloaded dictionary aliases and personal learned words
have separate lifetimes; see the shared UI removal contract. The tray/spelling
fixtures cover persistence and cache-only restart, not every signed-in composer.

## External links

Settings use fixed URL identifiers with Electron `shell.openExternal`.
Discord's `saferShellOpenExternal` retains its protocol policy. Main/popout
`will-navigate` must forward cancelled cross-origin HTTP(S) anchors through that
helper, separately from `setWindowOpenHandler`; otherwise current-window links
are swallowed. Require a currently loaded HTTPS Discord document matching the
window's configured origin. Preserve same-origin navigation, popout allowlists,
redirect handling and the spelling exception. No custom browser launcher or
MIME association change is needed for this fix.

Bounded `external-link` diagnostics record request number, requested/submitted/
failed phase and elapsed time without URLs or exception strings. Submitted means
Electron launched `xdg-open`, not that a browser loaded the page. The intermittent
all-links failure has not been reproduced under instrumentation; the deterministic
navigation fix must not be described as resolving that incident. Correlate native
events with a process trace taken during an actual failing state before changing
openers. The inbound `discord://` queue is a separate path.

Registration is explicit: check the effective XDG desktop entry's executable
against the launcher. Do not claim associations during status/startup. Managed
links use persistent `DPE_LINK_COMMAND`, not the temporary restart script;
profile/version ownership is described in [development](dev-environments.md#discord-link-registration).

Real fixtures on Canary 1.0.2472/2473 and Electron alpha.1/alpha.6 reproduced four
swallowed anchor cases and dispatched all twelve after repair. Internal
navigation stayed internal; an owned recording opener received exact query and
fragment. Evidence: `out/external-links-20261112/{2473-final,settings-links-final}/`.
Alpha.6 executable hash is
`aae75f74669750b506f86111426452d7b1f603f6632d4e277171ac352908b790`;
2473 core ASAR hash is
`0e00a8215fd588fee2b3b5002d5a4a867f9daa99eb4045ff3233e8607c3424d7`.
Use the extracted bundle with `tests/external-links/main.cjs`, `DPE_LINK_CORE`
and fresh `DPE_LINK_OUTPUT`; `DPE_LINK_BASELINE=1` asserts the original failure.
Use a verified default-app-capable test runtime: the installed embedded app may
ignore script arguments. The focused settings harness uses `external-links`;
the broad harness's preceding hover-color assertion remains a separate failure.

## Popout lifecycle

Discord popouts need a working same-origin opener handshake. Matching the existing
main renderer's sandbox option fixed Electron 46 severing `window.opener`; keep
Node disabled and context isolation intact. No global sandbox disable is needed.
The desktop checker opens the real `/popout`, receives its loaded message and
verifies parent rendering plus close/reopen on fresh login pages.

Immediate close/reopen of a named video window reproduced 12/12 disappearing
replacements on alpha.6. A 250 ms control survived 12/12; it established ordering,
not a production delay. The page adapter releases the browsing-context name
before native close. Native close-state tracking allows replacements, and old
closed callbacks remove a registry entry only if still its owner. Cancelled
closes restore reuse/name and preserve Electron's unload semantics. Tests require
all replacements to stay registered, retain opener, deliver frames and expose
no Node globals. This synthetic race does not retrospectively identify an
uncertain reported click. Evidence:
`out/popout-reopen-final-{wayland,x11}-20261111/result/report.json`.

## Popout placement

`call-pip.cjs` adds a separate PiP control immediately before the native call
popout control. Reuse the validated native control element (including tooltip,
classes, focus and disabled styling) and the original call open callback, retaining
native activity confirmation/ticket refresh. The regular control clears pending
PiP intent and invokes its native action. Omit the extra control for unrelated
activity/debug callbacks; disable it while a call popout is already open.

The in-app video PiP tray also offers PiP immediately after its native settings
cog (beside the native popout when the cog is absent). Discover the native view
export and validate its returned class/control methods before extending that
instance's tray. Preserve the native controls, callback and class lifetime; use
their existing icon classes/colors on video rather than the main toolbar color.
Both entry points disappear while the separate PiP is active. Unknown view
structures remain native. The component harness covers ordering, inherited
color, keyboard activation and ordinary popout behavior; it is not a live-call
visual qualification.

Entry visibility subscribes to the owned PiP lifecycle. Poll only its live
WindowProxy until native close finishes, then refresh mounted controls even
when Discord's PureComponent props did not change during pop-in. Autofocus also
runs at the outer native call view and retries on RTC store changes, so shared
guild-room state or late media cannot prevent initialization. Explicit PiP uses
the native media-tile view instead of the shared guild room; APP and ordinary
popouts retain that room. An empty fullscreen layout still needs a selection.
Restore an automatic selection on close only when it remains unchanged.
An explicit in-app PiP click carries its displayed participant as the preferred
media target for that owned window and channel, validated against eligible RTC
media before selection. Own shares remain excluded. Clear this transient origin
on close; ordinary overlay popout clicks clear any unfinished PiP intent.

An explicit PiP click arms a one-use, 60-second call-window intent in the opener.
The existing same-origin /popout lifecycle wrapper consumes it only for the call
window key, appends `dpe-pip=yes` to that exact window.open request and marks the
returned WindowProxy. Native `popoutOptions` receives the original feature string
separately from Discord’s parsed/allowlisted features. Without that marker it
returns the ordinary global window options, without PiP geometry, pinning or
floating hints. Main-frame injection confirms the page-world PiP marker from the
owned native window options. Portal filtering/autofocus and hover controls require
that explicit mode. Normal button clicks, errors, pagehide, consumption and expiry
clear pending intent. Callbacks from a cancelled native confirmation expire; native
confirmations left open beyond the bound retain ordinary popout behavior.

PiP automatic fullscreen and member-bar changes restore the previous native
POPOUT layout/member state on close when those values still match the automatic
changes. Keep close/reopen identity safety for ordinary and PiP call windows.
The account-free real desktop-core fixtures cover normal/PiP creation and rapid
close/reopen on private Niri and X11 displays in `out/pip-required-native-*`.
`out/pip-button-settings-final/` covers the separate controls’ real component
rendering, keyboard activation, spacing and disabled state. Live-call toolbar
appearance remains unverified by those synthetic controls.

Default placement anchors independently of media discovery, then refits after
metadata/committed resizing. Resolve the owned child's actual output; fit small
outputs and account for visible borders. Revalidate identity, PID, focus, normal
floating state and geometry before every asynchronous move. Manual move/resize,
focus loss, workspace change or fullscreen cancels pending automatic placement.
Do not mistake mapping/own resize notifications for a manual change.

Every new/reopened PiP measures the current dominant visible video, even when
stored geometry exists. Start at a bounded 16:9 size if no video is available.
Centered aspect-fit letterboxing is part of the available video slot, not fixed
chrome; subtract only actual surrounding UI and native decorations when fitting
source dimensions. Never capture pixels to determine the aspect ratio.

PiP always requests floating, automatic corner placement and topmost behavior.
The former `popoutFloating`, `popoutAutoPosition` and `popoutAlwaysOnTop` options
are removed from settings/IPC normalization; old saved false values are ignored.
The regular popout provides the normal-window alternative. `popoutCorner`
validates four corner names, with bottom-right default. Reopened PiP windows also
anchor automatically. Corner
saves move an open PiP only while it still matches its previous corner and
baseline size. Explicit picker actions may move a manually placed window;
geometry is revalidated before dispatch. Stable owned IDs allow these checks
without requiring focus after the initial match. KWin background queries remain
PID/UUID restricted.

Revalidate window lifetime after each awaited compositor snapshot before calling
native window methods. A corner query that completes after the PiP closes returns
`available: false`; stale geometry must not reach a destroyed BrowserWindow.

PiP always requests topmost; its native optional pin control is hidden only in
PiP through the WindowContext boundary. Ordinary popouts retain that control.
The validated native setAlwaysOnTop IPC call site also passes through the desktop
controller: force true only for an owned explicit PiP, retaining native values for
ordinary windows. This prevents Discord’s initial regular-window pin preference
from undoing PiP’s required pin after construction. Electron supplies X11 stacking; KWin uses
`keepAbove` on the verified window. Native Wayland compositors without a stacking
API retain their policy; do not repeatedly raise/focus windows to emulate it.
`popoutHideTitlebar` overrides native titlebars for floating video windows.
Changing a native frame requires reopening; custom chrome updates live.
Video popouts override a renderer-supplied `resizable=false`. The fixed mapping
hint is temporary; normal mode restores unrestricted maxima and a bounded
160×90 minimum (the global minimum-size opt-out still applies). Keep compositor
edge/corner resizing available, including after fullscreen. Do not inject custom
resize grips or a renderer resize IPC bridge, reset constraints repeatedly during
native grabs, or intercept pointer gestures for resizing.

Native and HTML fullscreen share the same normal-geometry capture/restore path.
Keep client bounds separately from compositor decoration bounds; fullscreen
geometry must not overwrite the automatic-size baseline. Restore normal resize
constraints after both fullscreen modes. On Hyprland, a native fullscreen exit
can leave a maximized client hint: clear it only for the previously identified
owned PID/address after native exit, then restore committed geometry. Do not
infer a new window from focus/title while recovering.

The PiP hover picker uses a frame-local, origin-checked preload bridge with only
state/choose/settings methods. Main-frame IPC accepts only registered owned floating
video popouts at trusted Discord `/popout` URLs and four validated corner names.
The parent renderer owns Discord’s call PiP React portal. Inject
`pip-participants.cjs` in the parent and identify the native WindowContext by its
renderWindow/appContext/windowDispatch contract. Apply filtering only when that
context is POPOUT and its live renderWindow has the native windowKey (or browsing
context name) DISCORD_CHANNEL_CALL_POPOUT.
A child-document store override cannot affect the parent’s portal render.

Wrap the validated native participant view’s exported sizing wrapper and adjust
only its returned participant element. Keep native hooks, sizing and unrelated
exports. Native export namespaces may contain frozen data properties as well as
getters: use a separate proxy target with delegated descriptors, not a proxy that
violates frozen-property invariants. Reject ambiguous stores/contexts/actions and
unknown view trees; do not rewrite React factories or inspect private fibers.
`popoutShowNonVideoParticipants` defaults true. Build PiP’s participant arrays
from the full native participant collection, retaining cameras, shares and
activities; omit voice-only participants when disabled and always omit items
popped out elsewhere. Keep the regular store/getters/persisted toggle intact.
The native `no-video-hide` menu item uses the same WindowContext-scoped local
value/action. DPE saves update mounted boundaries through local subscriptions;
unrelated saves preserve the window-local override. Unmount removes subscriptions.

Auto-focus runs after the validated PiP view mounts/updates, once per window and
channel, and stops on pointer/keyboard input or pagehide. Deduplicate eligible
IDs, explicitly select even an automatically resolved grid participant and set
the native `full-screen` layout in POPOUT only. Preserve existing fullscreen
views. Exclude the current user’s screenshare using UserStore identity and stream
ownership; unknown identity/ownership leaves native selection intact. Cameras
(including self-camera), remote shares and activities count toward the sole-item
decision. This changes Discord’s content layout, not Electron/OS fullscreen.

The public signed-out shell and its call-PiP static chunk graph supplied the
current view, sizing-wrapper, context and checkbox contracts without account or
call actions (`out/pip-participant-contract/video-factories.json`). Adapter tests
cover parent/PiP isolation, frozen exports, the sizing wrapper, live/local saves,
activity retention and own-share exclusion. The real settings/DOM harness checks
controls, prompt layout and monitor-arrow states in `out/pip-monitor-settings-verified/`.
Live-call rendering remains unverified by the synthetic fixtures.

Monitor arrows expose only left/right operations through the frame-local bridge.
IPC accepts `monitor-left`/`monitor-right` only for an owned floating PiP at the
trusted /popout origin. Determine neighbors in logical output coordinates, skip
mirrors and aligned vertical stacks, and never wrap. Native Niri/Sway/Hyprland/KWin
output lists take precedence over Electron’s Wayland screen coordinates. Move only
the previously identified owned window, revalidate output/geometry before transfer,
then fit without stretching and apply the same corner using the destination’s
reserved area/insets. X11 uses Electron’s logical displays/work areas. Unsupported
Wayland placement (including GNOME) disables both arrows. Compositor IPC failures,
removed outputs, fullscreen and closed windows leave native placement intact.
The private mixed-scale Sway check exercises a monitor roundtrip through the real
preload/IPC bridge (`out/pip-monitor-sway-verified/`); unit checks cover Niri output
identity/revalidation and X11 shrinking/directional state. KDE’s transfer uses
[sendClientToScreen](https://develop.kde.org/docs/plasma/kwin/api/).

The paired empty Invite to Voice/Choose Activity tile is hidden only in video
popouts. Require both prompt controls in one tile and no active media/participant
content; collapse its outer single-child tile wrapper. Reflow only an identified
sibling media grid, retaining the native gap and updating on resize; restore native
layout when activity/media content mounts or the prompts disappear. Prefer semantic
`data-action` markers; the bounded text fallback currently recognizes English.
Other localized unmarked placeholders retain native behavior. No React factories
or controls outside the PiP are hidden for this cosmetic adapter.

One DOM adapter displays four 16:9 rectangles using shared component CSS/tokens;
it avoids React/webpack hooks. Attach beside a unique status within its existing
noninteractive container so offsets do not chase animated screen coordinates.
If that container owns an opacity transition, inherit its fade rather than
multiplying it by another transition. The standalone fallback uses a reversible
150 ms opacity transition. Borderless rectangles use the shared secondary/brand
control tokens; keep visible insets even around expanded hit targets.
Recover a removed/hidden status anchor into the body, reattach when it returns,
and recover state on focus/fullscreen exit. A bounded DOM repair uses the last
state without extra compositor IPC. Ignore offscreen/hidden videos and allow a
dominant video with a thumbnail; ambiguous equal tiles still hide the picker.
It polls geometry only while hovered/focused,
clears highlighting after size/position changes, and hides for ambiguous media
or windows covering at least 70% of usable display area. Display flags and PiP
settings share `SelectField` and the same select upgrader.

Keep the automatic client-size baseline separately from decorated bounds.
Explicit corner clicks restore that size before moving and highlighting; settings
changes still preserve manual sizing/placement. Late Discord minimum-size IPC
must not stretch floating PiP beyond its bounded minimum or invalidate its baseline.

The default margin on other backends is 16 logical pixels inside the usable
area. Niri uses `max(0, gap + side strut + 6)` on every edge: six pixels keep
separation from normal window edges, without imposing a minimum configured gap.
The zero clamp protects panel reservations. Carry per-edge values through
`workArea.pipInsets` so placement and highlighting share the same geometry.

| Backend       | Placement contract and limit                                                                                                                                                                                                                                                                                                                                                                                                                   |
| ------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Niri          | Decorated tile size/output-local relative moves; preserve sign of fractional negative zero. Calibrate the panel-reserved working area through bounded center/fixed moves of only the verified owned PiP, then cache per output geometry. Read active config gaps/struts with ordered includes/workspace overrides; each inset is max(0, gap + side strut + 6), without a minimum gap. Initial map/calibration may show before final placement. |
| Sway          | Containing workspace rectangle includes gaps/panel reservations; accept our own resize/move notifications. Negative/fractional output origins are covered.                                                                                                                                                                                                                                                                                     |
| Hyprland      | Revalidate owned floating/fullscreen state rather than Wayland's maximized hint. Wait for committed resize; account for monitor reservations, rotation, scale and global float gaps/borders. Support validated older commands/current Lua dispatchers. Per-workspace extensions remain outside the global query.                                                                                                                               |
| KDE Plasma 6  | Bounded temporary KWin script identifies the initial active PID/stable UUID, then reads only that owned UUID for background controls; checks previous decorated frame and MaximizeArea. Authenticate D-Bus callbacks against KWin's unique owner; unload scripts/remove temporary files. Optional dbus-python/PyGObject absence falls back to compositor placement.                                                                            |
| GNOME Wayland | Resizable-dialog sizing begins at ready-to-show without initial focus; subsequent real blur/resize cancels it. Shell owns position. Do not enable unsafe Eval or install an extension.                                                                                                                                                                                                                                                         |
| X11           | Electron/EWMH work area and WM panel reservations; real WM testing matters beyond bare Xvfb.                                                                                                                                                                                                                                                                                                                                                   |

Private Niri/Sway/Openbox fixtures cover animation, 125%/150% scale, negative
origins, 640×480 secondary output and panel reservations. KWin 6.8.2, GNOME 51.1
and Hyprland 0.57.2 have private synthetic runtime checks with separate HOME,
runtime dirs, displays and D-Bus; no host VT/modeset/session socket is inherited.
KWin was tested without a full Plasma shell/panel; older Hyprland syntax has unit
coverage. No real call was needed. Reports live under
`out/popout-position-expanded-*`, `out/popout-kwin-clamp-final-20261112/`,
`out/popout-gnome-startup-*`, `out/popout-hyprland-*` and
`out/pip-corner-settings-final-20261112/`. Use `scripts/check_popout.py --help`
for `--placement-only`, `--compositor`, `--scale`, `--corner`,
`--panel` (private Niri layer-shell panel),
`--compact-top` (gaps 10/top strut -10), `--tools-prefix`
and real X11 WM options. Niri configuration reads are bounded and fail back to
the measured working area when the supported KDL subset cannot be parsed. Changes
to panel reservations while a PiP is open are not automatically recalibrated.

Current PiP regression inputs use Electron 46.0.0-alpha.6
(`aae75f74669750b506f86111426452d7b1f603f6632d4e277171ac352908b790`)
and working-tree adapters in `tests/popout/placement.cjs`. Private Niri at 125%
with a real 64px layer-shell panel and X11/Openbox cover reopening with a current
portrait stream, aspect accuracy within one client pixel, native/HTML fullscreen,
manual sizing/placement preservation, unrestricted native resize constraints and
absence of custom resize controls/IPC. The same checks pass on KWin 6.8.2 and
Hyprland 0.57.2; X11/KWin verify default topmost and live opt-out.
Reports: `out/pip-native-resize-{niri,x11}-checked-20261114/result/report.json`
and `out/pip-native-resize-{kwin,hyprland}-20261114/result/report.json`.
Private panel checks on Electron 46.0.0-alpha.10
(`802ff9e279c229f100cea73fb3b2bb161f1a54139041c0b24bbe974cbed70d18`)
at 125% scale also cover a 6px top inset with unchanged
side/bottom margins and the existing larger positive-strut placement:
`out/pip-gap-controls-niri/result/report.json` and
`out/pip-gap-controls-standard/result/report.json`.
The settings harness covers default-on topmost, shared selects, titlebar controls,
keyboard/narrow/zoomed layout and letterboxing versus actual titlebar measurements
in `out/pip-native-resize-settings-final-20261114/`. These synthetic checks do not
establish live-call behavior.

## Desktop media controls

`openasar.desktop.mediaControls` defaults false; startup disables
HardwareMediaKeyHandling and MediaSessionService before readiness in both stages,
removing only conflicting enables. Opt-in preserves explicit user flags and
requires restart. A Chromium dependency patch filters only audio-session
publication for exact Discord `/assets/` and `/sounds/` HTTPS resources while
retaining attachments, blobs, embeds, videos and persistent voice-message controls.
It does not alter playback/routing or renderer audio tracks. That native filter
needs its matching runtime; the older runtime's successful default-off/MPRIS
probe does not qualify the source-only patch. Evidence:
`out/desktop-media-native-20261105/`, `out/media-controls-policy/`.

## Diagnostic export

Restricted settings IPC invokes a Python worker, not collection on Electron's
main thread. GPU queries have a 1.5 s deadline, systemd commands 5 s, each text
log 32 MiB and total input 128 MiB with a 45 s collection deadline. Gzip input is
bounded before redaction. Publish a complete private ZIP atomically; file-only
portal permissions use a temporary-file fallback, preserving old output on failure.

Redact every text/JSON/archive-name/error path and strip ANSI escapes. Exclude
account stores, settings/caches, binary logs, raw cores/minidumps, command lines,
environments, images and credentials. Crash summaries are bounded metadata only.
Redaction is best effort for unlabelled prose; users review before sharing.
Export performs no upload. Diagnostics must identify absent evidence rather than
invent clean results. Real worker/settings coverage is in
`out/export-logs-redaction-final/`; `DPE_SETTINGS_SCOPE=troubleshooting` exercises
IPC, cancellation, ownership, errors and focus without an account.

PiP refinements use the shared interactive icon color token rather than an icon
component’s literal fallback color. The call entry is omitted inside a PiP
WindowContext. A single visible participant uses the native focused tile without
a duplicate participant strip. The recognized hidden-video placeholder names
PiP explicitly and its capture-phase button opens the trusted PiP-only settings
IPC alias; Settings maps `pip` to Appearance and focuses the PiP heading.
Niri monitor transfers address the destination active workspace by IPC Id with
`focus:false`; Hyprland uses silent/follow-false transfers, and Sway moves to the
destination workspace without following it. No cursor restoration command or
user compositor configuration changes are made.

The corner picker is available throughout PiP, including empty participant
screens, and no longer depends on mounted media. Hide the monitor-arrow row when
both directional targets are unavailable (single output or unsupported compositor),
while retaining corner controls. The opener tracks its live PiP WindowProxy so
the PiP entry is omitted in all call toolbars until that window closes; the
ordinary return-to-call control remains native.

Monitor transfers preserve the preferred client size and normalized position
between the usable corner limits. Exact corners stay anchored; free placement
is carried to the same relative location. When a destination is too small, fit
proportionally but retain the preferred size/position for the return trip. A
manual move or resize invalidates that transfer checkpoint. PiP’s settings action
opens an independent normal Settings window, not a child dialog. Its native button
label markup is retained and the shared button padding/nowrap treatment applies.
