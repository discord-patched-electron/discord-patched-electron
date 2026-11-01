# Cross-desktop screen-share audio detection

The helper still requires exactly one PipeWire video source linked to this
Discord native process. It never treats a video node ID as a PID, selects the
focused app, compares window titles, or broadens an unknown window into system
audio. Existing niri and native-PID detection remain available.

## Detection paths

`portal-detection.cjs` starts a bounded `busctl --user --json=short monitor` around
the native screen picker. Match rules cover portal Response signals and replies
from known desktop backends. D-Bus method replies do not carry an interface, so
backend reply matches are service-scoped and the parser accepts only successful
`ua{sv}` results containing a single stream. Unsupported shapes are discarded.
Only app IDs, window IDs and stream identifiers survive parsing; window titles,
restore tokens and raw messages are never logged or saved.

- Standard portal `source_type=1` identifies monitor sharing. Region/virtual
  sources do not automatically become system audio.
- GNOME v1, KDE v1 and Hyprland v3 restore metadata can identify the selected app.
  Multiple selected streams and unsupported versions remain manual.
- Mutter's `RecordWindow` call is joined to its method reply using caller and
  reply cookie, then to `PipeWireStreamAdded` using sender and stream path.
  Window Calls supplies the live PID for that exact window ID. If the extension
  is absent, a portal app ID may still resolve through audio metadata.
- KWin's `kwin-screencast-<desktopFileName>` stream media name can match a live
  `kdotool` window even without restore metadata. Output/region names without a
  matching KWin application are rejected.
- Hyprland's `hyprctl` inventory resolves app classes; GNOME Window Calls and
  KWin's optional `kdotool` provide their corresponding live inventories.
  Exact application IDs and process binaries in playback metadata can resolve
  an app without desktop tools. Separate process families with the same app ID
  are ambiguous and require a manual choice. Flatpak PID resolution still uses
  the verified sandbox instance path, never the namespace PID.

The observer stops after two minutes or cancellation. A small Python exec wrapper
sets parent-death SIGTERM so a renderer crash cannot leave the monitor running. Unbound observations expire
in 15 seconds and the record count, input size and variant nesting are bounded.
The helper binds observations to node serials and pins the selected process's
PID/start time. Exited/reused processes cannot silently change the selected app.
A short 1.2-second detection window allows portal replies to arrive; once the
manual picker opens, late metadata cannot override its selection or cancellation.
Source changes and helper exit retain existing generation-based cleanup.

`openasar.audioAutoDetect` and `openasar.audioSkipScreenPicker` both default to true
in the canonical profile. The settings switches use main-frame-scoped IPC, and
the adapter rereads them for each share configuration. Application matching and
confirmed monitor sharing can be disabled independently. Either enabled option
keeps the portal observer available. Disabling both, or launching with
`DPE_AUDIO_AUTO_DETECT=0`, always uses the manual picker. These settings cover the
Wayland adapter; the opt-in X11 native path remains unchanged.

Application identity is separate from playback availability: a live identified
app can wait indefinitely for its first playback stream. Known XWayland and
portal broker executables do not identify the hosted app. niri's satellite
windows can use the XRes path below; unresolved brokers open the picker.
The 1.2-second grace period above waits for source metadata, not audible playback.
Ambiguous compositor
or portal metadata cannot fall back to a numeric hint, and exact app IDs must
agree across window and audio process families. The confirmed monitor path is
evaluated before hints. A generic `screen:` ID does not establish a whole-screen
share. See the [confidence regression evidence](wayland-audio.md#unmatched-application-playback-31-october-2026).

## Upstream contracts checked

- [Portal stream properties](https://github.com/flatpak/xdg-desktop-portal/blob/main/data/org.freedesktop.portal.ScreenCast.xml)
- [KWin window stream naming](https://github.com/KDE/kwin/blob/master/src/plugins/screencast/screencastmanager.cpp)
  and [PipeWire stream construction](https://github.com/KDE/kwin/blob/master/src/plugins/screencast/screencaststream.cpp)
- [KDE portal restore data](https://github.com/KDE/xdg-desktop-portal-kde/blob/master/src/screencast.cpp)
- [GNOME portal restore data](https://github.com/GNOME/xdg-desktop-portal-gnome/blob/main/src/screencast.c)
- [Mutter ScreenCast API](https://github.com/GNOME/mutter/blob/main/data/dbus-interfaces/org.gnome.Mutter.ScreenCast.xml)
- [Hyprland portal selection and restore metadata](https://github.com/hyprwm/xdg-desktop-portal-hyprland/blob/HEAD/src/portals/Screencopy.cpp)
- [Window Calls](https://github.com/ickyicky/window-calls)
- [Satellite's WM_CLASS and WM_S0 mapping](https://github.com/Supreeeme/xwayland-satellite/blob/main/src/xstate/mod.rs)
- [XCB XRes client-ID API](https://xcb.freedesktop.org/manual/group__XCB__Res__API.html)

Research snapshots are in `out/audio-detection-research/`. These contracts are
capability checks, not a promise that every released portal supplies the same data.
Sway/wlroots and COSMIC gain the standard monitor path; their unidentified window
and region shares remain manual. Older Hyprland restore v2 lacks an app class.
Portal restore information can also be absent when remembering selections is off.

## XWayland ownership resolution, 1 November 2026

`x11_owners.py` queries a local X server through XCB and XRes 1.2. It never
reads `_NET_WM_PID`: that property is supplied by the client and can contain
a sandbox-relative PID. XRes identifies the process owning the actual window
resource. Only niri targets with a known proxy and exact `app_id` enter this
path. Satellite copies the second nonempty WM_CLASS field, falling back to the
first; the probe follows that contract without title, focus, substring or
case-insensitive matching.

The owner of the `WM_S<screen>` selection must resolve through XRes to the same
PID/start time as the selected proxy. This prevents using a different DISPLAY.
Socket peer credentials alone are insufficient: niri can create the listen
socket before passing it to Xwayland, so the peer PID can be niri itself.
Every matching root-child window must have a live same-user owner in one process
family. Two unrelated instances are ambiguous even if only one is playing.
Reparented windows on other window managers are deliberately unsupported here.

The probe compares two full class/owner snapshots and checks the window inventory
and manager again. This catches a previously unrelated window acquiring the
matching class between reads. One immediate retry accommodates a disappearing tooltip.
A two-second child-process timeout bounds a hung X server; missing XCB/XRes,
remote DISPLAY, stale identities and failed queries fall back to the picker.
The controller caches the resolved PID/start time for this source and never
remaps it after exit or PID reuse. It then uses the existing app-family route,
which waits through silence and follows later playback children. Explicit
manual selection or cancellation remains authoritative.

Read-only inspection of the reported Nuvio window confirmed that niri identified
satellite while XRes identified Nuvio itself for both matching X11 windows.
That process family also matched the existing embedded mpv playback stream.
No media names, window titles, audio or account data were recorded. This proves
the metadata path on this host, not a live Discord call.

The private Xvfb fixture covers server-verified PID despite a false
`_NET_WM_PID`, exact class matching, wrong-manager rejection, duplicate-instance
fallback, disappearing instances and unrelated windows. Run it with:

```sh
DPE_X11_FIXTURE=1 xvfb-run -a uv run python tests/audio/x11-owners-smoke.py
```

The application-audio workflow runs the same fixture in its isolated container.
Unit tests additionally cover delayed Nuvio/mpv playback, child processes,
PID reuse, cancellation, missing dependencies and a hung probe. Packaging ships
the probe as a fingerprinted voice sidecar and recommends `libxcb-res0` on Debian.

Validation for this change passed: 485 Python tests, 197 JavaScript tests, lint,
the Xvfb fixture on host and inside the CI audio image, and all nine native audio
phases on each of PipeWire-Pulse and classic PulseAudio. Both servers retained
their default devices and had no remaining private routes or capture streams.
The Arch packaging fixture also passed. Evidence is under
`out/validation-storage/xres-share-*`; the rebuilt portable app is
`out/validation-storage/xres-share-app/`, with the new helper's staged bytes
verified against the source. `out/launch-test.sh` points to that build.

## Ownership reliability pass, 1 November 2026

The follow-up audit found and fixed these failure paths:

- A previous route could remain active while resolving a different source or
  proxy. Source transitions now silence and close it before the blocking query.
  After XRes returns, the helper checks the linked video source and niri's
  selected window again before attaching playback.
- niri IPC failure could allow a weaker native hint to replace the compositor's
  selection. A configured niri backend now remains authoritative on failure.
  Unknown reply tags, malformed target variants and unreadable executables
  require manual selection. A receive deadline bounds trickled IPC replies.
- Routing errors and temporary missing video links could discard process bindings
  or manual consent. Suspension now closes resources while retaining those
  bindings, including cancellation. A pending picker gets a new request ID on
  recovery, so old replies cannot apply to its replacement.
- The X11 child could outlive a killed routing helper. Parent-death SIGKILL now
  terminates it even while XCB is blocked. Normal queries retain their two-second
  timeout. Two complete ownership snapshots replace adjacent per-window checks.

The real Xvfb fixture now freezes an explicitly owned second X server, kills the
query's parent, disables XRes, and repeats ten duplicate-instance creation and
destruction cycles. The frozen-server query returned in approximately 2.005
seconds inside the CI audio container; the orphan test reaped the terminated
probe. It never stops or inspects the user's X server.

Native regression checks passed all nine phases in each of automatic
PipeWire-Pulse, automatic classic PulseAudio, and manual PipeWire-Pulse modes.
All three left default devices unchanged and no private routes or capture streams.
Tests additionally cover a source/window changing during resolution, PID reuse
after recovery, cancellation surviving failure, stale picker replies and class
changes with an unchanged X11 window-ID inventory. Synthetic-process fixtures
now supply explicit executable paths rather than consulting real host PIDs.

Evidence is under `out/validation-storage/xres-reliability-*`. The portable build
is `out/validation-storage/xres-reliability-app/`; `out/launch-test.sh` is updated
after its staged helpers are checked against the current source.

Remaining limits: these checks exercise local ownership and routing, not a live
Discord call or every compositor/sandbox combination. XRes correlation currently
supports niri/satellite's class contract. Duplicate independent process families
and unavailable metadata deliberately remain manual. Window/process metadata is
not a security boundary against a malicious application running as the same user.

## Validation

- Unit coverage includes portal formats, ambiguity, unrelated streams, serial
  reuse, process exit/reuse, source generations, bounded fallback, manual
  cancellation, missing tools and disabled detection.
- `tests/audio/portal-observer-smoke.py` runs synthetic portal/Mutter services on
  a private D-Bus session and uses the real `busctl` observer. It covers method
  reply encoding, caller/cookie matching, signal matching and monitor selection.
  Run with `dbus-run-session -- uv run --with dbus-next==0.2.3 python
  tests/audio/portal-observer-smoke.py`. dbus-next is only a test dependency.
- `out/audio-autodetect-settings-check/settings.json`: retained Electron runtime,
  fresh profile, real settings UI/IPC. The switch saves and survives tab changes,
  and its saved value is read by the capture adapter. Existing settings tests
  include narrow layouts, keyboard navigation and 200% zoom.
- Full checks passed: 395 Python tests, 131 JavaScript tests and `bun run lint`.
  Full-suite and lint logs: `out/audio-autodetect-tests.log` and
  `out/audio-autodetect-lint.log`.

No live calls, account login, camera capture or desktop screenshots were used.
Full GNOME, Plasma, Hyprland, Sway and COSMIC sessions remain unverified. A missing
or prohibited monitor and unknown portal schema fall back to manual selection.

## Writing review

| Severity | Location                                                              | Before                                    | After                                                       | Why                                                                       |
| -------- | --------------------------------------------------------------------- | ----------------------------------------- | ----------------------------------------------------------- | ------------------------------------------------------------------------- |
| MEDIUM   | `docs/wayland-audio.md:9`, `integration/audio/window_inventory.py:12` | Other desktops described as always manual | Capability-specific detection paths and optional tools      | Keep guidance consistent with actual behavior and give a useful fallback. |
| LOW      | `integration/updater/config-html.cjs:14`                              | No detection control                      | Plain toggle label with Wayland scope and next-share timing | Explain what changing the setting does without implying a restart.        |

Reviewed the setting, helper guidance and user docs against implemented behavior.
No actionable writing findings remain. Approve — inspected strings only.
