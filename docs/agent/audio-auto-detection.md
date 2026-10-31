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

`openasar.audioAutoDetect` defaults to true in the canonical profile. The native
settings switch writes it through the existing main-frame-scoped settings IPC.
The adapter rereads it for each share configuration. False bypasses niri, native
hints and portal detection, preserving the manual picker and audio routing.
`DPE_AUDIO_AUTO_DETECT=0` temporarily forces the same behavior. This setting covers
the Wayland adapter; the existing opt-in X11 native path remains unchanged.

## Upstream contracts checked

- [Portal stream properties](https://github.com/flatpak/xdg-desktop-portal/blob/main/data/org.freedesktop.portal.ScreenCast.xml)
- [KWin window stream naming](https://github.com/KDE/kwin/blob/master/src/plugins/screencast/screencastmanager.cpp)
  and [PipeWire stream construction](https://github.com/KDE/kwin/blob/master/src/plugins/screencast/screencaststream.cpp)
- [KDE portal restore data](https://github.com/KDE/xdg-desktop-portal-kde/blob/master/src/screencast.cpp)
- [GNOME portal restore data](https://github.com/GNOME/xdg-desktop-portal-gnome/blob/main/src/screencast.c)
- [Mutter ScreenCast API](https://github.com/GNOME/mutter/blob/main/data/dbus-interfaces/org.gnome.Mutter.ScreenCast.xml)
- [Hyprland portal selection and restore metadata](https://github.com/hyprwm/xdg-desktop-portal-hyprland/blob/HEAD/src/portals/Screencopy.cpp)
- [Window Calls](https://github.com/ickyicky/window-calls)

Research snapshots are in `out/audio-detection-research/`. These contracts are
capability checks, not a promise that every released portal supplies the same data.
Sway/wlroots and COSMIC gain the standard monitor path; their unidentified window
and region shares remain manual. Older Hyprland restore v2 lacks an app class.
Portal restore information can also be absent when remembering selections is off.

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
