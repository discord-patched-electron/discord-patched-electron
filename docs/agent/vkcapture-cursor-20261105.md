# Vkcapture cursor: feasibility and implementation, 2026-11-05

## Decision and scope

Keep game capture picker-free, as requested. Implement the standard
`ext_image_copy_capture_manager_v1.create_pointer_cursor_session` path using an
exact foreign-toplevel source. Automatic source resolution currently supports
Sway IPC with `foreign_toplevel_identifier`, one mapped window belonging to the
authenticated hook process, and unchanged process identity. This is experimental,
not desktop-wide qualification. Other Wayland desktops remain unsupported for
this game cursor path; monitor/portal capture remains a separate user choice.

The application hook's swapchain is not a compositor capture source. A Wayland
object ID is connection-local; an X11 window ID, process name, title or app ID
cannot be passed as an `ext_foreign_toplevel_handle_v1`. Matching a title or
choosing the first monitor could overlay another window's cursor. Native games
with helper processes, multiple mapped windows or mismatched source dimensions
therefore receive no overlay from this implementation.

## Research

Upstream obs-vkcapture's [Wayland cursor implementation](https://github.com/nowrep/obs-vkcapture/blob/master/src/wlcursor.c)
still uses its older `ext_screencopy_manager_v1` protocol, captures per-output
cursor data and renders the first output reporting a cursor. That interface is
not the standardized image-copy interface, and a monitor cursor position is not
a game-window position. DPE previously implemented only asynchronous XFixes
queries and could not capture a native Wayland cursor.

Reviewed upstream reports include [missing Wayland cursor #135](https://github.com/nowrep/obs-vkcapture/issues/135),
[missing KDE game cursors #259](https://github.com/nowrep/obs-vkcapture/issues/259),
[stuck/visible cursor #230](https://github.com/nowrep/obs-vkcapture/issues/230),
[misalignment #35](https://github.com/nowrep/obs-vkcapture/issues/35), and
[X11 missing cursors #274](https://github.com/nowrep/obs-vkcapture/issues/274).
These reports identify failure modes, not proof that every compositor reproduces
one root cause.

The authoritative [image-copy XML](../../integration/video/protocols/ext-image-copy-capture-v1.xml),
[source factories](../../integration/video/protocols/ext-image-capture-source-v1.xml)
and [foreign-toplevel identifiers](../../integration/video/protocols/ext-foreign-toplevel-list-v1.xml)
are already pinned to wayland-protocols 1.50 in this repository. Cursor position
is relative to the captured source in transformed buffer pixels. Positions can
extend outside the source; clip the image rather than clamping the hotspot.
Hotspot updates become effective with the next cursor image's `ready` event.
Cursor leave immediately suppresses composition, including while an image
request remains pending.

The [ScreenCast portal](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.ScreenCast.html)
advertises available cursor modes: hidden, embedded and metadata. Metadata can
supply the actual cursor alongside a stream, but requires authorizing a screen
capture session; there is no portable cursor-only portal. Receiving and discarding
an entire companion stream also adds work. This route was excluded by the user's
picker-free requirement. Core `wl_pointer`, relative-pointer, pointer constraints,
cursor-shape and input-capture protocols do not provide another application's
cursor bitmap and its source-relative position. A locally loaded theme arrow
cannot reproduce application cursors, animation or hidden state.

## Desktop compatibility

This table separates protocol feasibility from the implementation's automatic
source matching. Runtime globals and exact source identity take precedence over
desktop/version strings. No desktop row is a live sender/receiver qualification.

| Desktop/compositor                                                          | Evidence and feasibility                                                                                                                                                                                                                                                                        | This implementation                                                                                                                                                      |
| --------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Sway                                                                        | [Server source](https://github.com/swaywm/sway/blob/master/sway/server.c) provides foreign-toplevel capture; [IPC source](https://github.com/swaywm/sway/blob/master/sway/ipc-json.c) exposes its identifier. Installed versions need both.                                                     | Automatic unique-process mapping; requires pointer-cursor sessions, ARGB SHM cursor buffers, matching dimensions and one seat. Native Wayland and Xwayland are eligible. |
| Hyprland                                                                    | [Image-copy implementation](https://github.com/hyprwm/Hyprland/pull/11709) merged February 2026. [Toplevel mapping protocol](https://github.com/hyprwm/hyprland-protocols/blob/main/protocols/hyprland-toplevel-mapping-v1.xml) is relevant to future exact identity work.                      | No resolver implemented or compositor qualification; unsupported. Protocol presence alone is insufficient.                                                               |
| niri                                                                        | [September 2026 implementation](https://github.com/niri-wm/niri/commit/849c576ffc902c9a6f249d9371a1e95fb63cf553) explicitly adds output/cursor capture without toplevel capture. [Screencasting guide](https://github.com/niri-wm/niri/wiki/Screencasting) describes portal and output capture. | Output-only support cannot identify the captured game; unsupported.                                                                                                      |
| KDE Plasma/KWin, GNOME/Mutter                                               | Portal metadata/embedded capture is the portable route. [Mutter portal API](https://mail.gnome.org/archives/commits-list/2021-November/msg04072.html) documents cursor metadata. No direct cursor/source combination qualified here.                                                            | Unsupported for picker-free game cursors, including Xwayland games.                                                                                                      |
| COSMIC                                                                      | [Compositor implementation reports](https://github.com/pop-os/cosmic-comp/issues/2383) establish image-copy activity, not correct game identity or cursor qualification.                                                                                                                        | No resolver or runtime qualification; unsupported.                                                                                                                       |
| labwc                                                                       | [Source declarations](https://github.com/labwc/labwc/blob/master/include/labwc.h) include foreign-toplevel capture; [configuration guide](https://labwc.github.io/labwc-config.5) covers access to capture globals.                                                                             | No exact game resolver or runtime qualification; unsupported.                                                                                                            |
| river, Wayfire, phoc, Mir, Jay, Treeland, Cage                              | Support differs by compositor revision and enabled globals. Sharing wlroots/Smithay ancestry does not establish game identity or working cursor sessions.                                                                                                                                       | Unverified; no resolver implemented.                                                                                                                                     |
| Gamescope/nested compositors                                                | A nested game's surface and the host compositor's toplevel are different identities and coordinate spaces. The upstream stuck-cursor report includes Gamescope.                                                                                                                                 | Unsupported; no host-output substitution.                                                                                                                                |
| Weston, Cinnamon/Muffin Wayland, other compositors                          | No direct game identity/cursor combination qualified in this pass.                                                                                                                                                                                                                              | Unsupported.                                                                                                                                                             |
| Xorg: KDE, GNOME, Cinnamon, Xfce, MATE, LXQt, i3, Openbox and other X11 WMs | XFixes supplies the actual server cursor; the hook supplies the X11 drawable. Geometry must match the captured image.                                                                                                                                                                           | Existing X11 path retained; error/disconnect clears cached metadata. No new desktop-specific qualification.                                                              |

Xwayland is not an authoritative Wayland cursor source: queries can retain the
last X11 position/shape while the pointer is over native surfaces. DPE now avoids
using XFixes in a Wayland session. On unsupported desktops this deliberately
omits the cursor rather than showing a stale or unrelated pointer.

## Implementation and GPU ownership

- The broker resolves the source once at producer launch. It validates the
  authenticated process identity before and after Sway's tree response and
  rejects ambiguous/missing identifiers. Wayland and Sway IPC sockets must have
  the same compositor PID and user credentials, preventing nested-session mixups. It forwards Wayland display credentials
  without inheriting game interposers. The internal identifier is not an
  arbitrary inherited environment override.
- A dedicated asynchronous Wayland connection binds only the needed interfaces.
  It requests the selected toplevel's constraints, never its image frames, and
  captures only the cursor. Multiple seats and registry churn fail closed.
- Cursor buffers are bounded to 256×256 ARGB8888. New constraints retire storage
  only after an outstanding frame completes. Stale-generation images are
  discarded; a constraints failure retries only with a newer generation.
  Unsupported formats/transforms, excessive sizes, source closure and connection
  failures suppress the overlay. Re-enter can reuse an unchanged image.
- Composition uses the existing GLES texture/blend path inside the owned output
  DMA-BUF, before its existing completion fence. Cursor composition disables
  direct relay for that stream, since forwarding an untouched game buffer would
  omit the overlay. Acquire/release and output-fence ownership remain intact.
- Main game pixels never enter CPU memory. CPU work handles protocol messages,
  a bounded cursor-image copy/upload when an image becomes ready, and GPU command
  submission. Cursor movement reuses the texture and changes placement. No
  full-frame readback, CPU pixel blending, busy fence waits or companion desktop
  stream was added. This is GPU composition with a small SHM cursor upload, not
  a claim that every compositor internally renders its cursor on the GPU.

Cursor updates appear with the next game frame. Cursor-only movement does not
start a new game render. Dimension mismatches hide the overlay instead of guessing
fractional scaling, decoration offsets or crop transforms. Unmapping/recreating a
window requires fresh producer/source resolution. Software cursors already drawn
by a game remain in its captured image; DPE adds no replacement theme cursor.

## Validation and remaining work

`bun run lint` and `bun run test` pass: 744 Python tests and 446 JavaScript
tests (443 passed, three skipped). The GPU fixture ran on the AMD render node.

- Strict native builds of source and preview helpers pass.
- Private libwayland client/server socketpair fixture passes: exact source,
  pointer session, real SHM pixel delivery, position/hotspot and teardown with a
  pending frame. The server asserts that no main-window image is requested.
- State fixtures cover hidden/re-enter, pending hotspot, image generation,
  out-of-bounds hotspot placement, source dimension mismatch, oversized images
  and preserving in-flight storage across changed constraints.
- The synthetic GPU fixture passes on `/dev/dri/renderD128`: placement, BGRA
  channel order, image replacement, premultiplied alpha, edge clipping and hidden
  state are checked in the framebuffer. Its test-only readback is 16×16 pixels.
- The complete capture bundle is rebuilt and hash/inventory validated through
  `scripts/stage_capture_backends.py`; generated outputs stay under
  `out/cursor-rework/`. No installed payload or running app is replaced.

The host session is niri. No live Sway compositor, Discord sender/receiver,
animated application cursor, fractional-scale transition or GPU performance
benchmark was qualified. The protocol and framebuffer tests do not establish
stream delivery or all-desktop correctness. Before promoting this beyond
experimental, run an owned Sway workload through the native sender/receiver and
verify decoded cursor placement/alpha, shape animation, focus/hidden transitions,
resize, multi-monitor movement, 60→30→60 FPS and teardown. Add separate exact
identity adapters and compositor fixtures before enabling further desktops.
