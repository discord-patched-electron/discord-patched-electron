# Screensharing settings and live diagnostics

The separate DPE Settings window now has a Screensharing tab built with the shared
Preact controls, icons and presentation tokens. Existing audio settings in General
remain compatible. Preferences live at `openasar.screensharing`:

- `captureMethod`: `auto` (default), `portal`, or `x11`.
- `capturePacing`: `true` by default; read by the native transport for each new share.

Only the runtime's supported method catalog enables explicit choices. Direct
Wayland capture and Vulkan/OpenGL game hooks are not presented as working backends.
Pacing still depends on the native capture adapter being installed; its rollout
and benchmark qualification are separate from this settings change.

## Picker routing

`integration/video/capture-policy.cjs` uses the shared page-world module watcher
to identify the existing `MediaEngineStore`. It wraps only
`getUseSystemScreensharePicker()`: Automatic returns Discord's existing choice;
explicit Portal requires a Wayland session and a positive
`supportsSystemScreensharePicker()` result; explicit X11 requires an X11 session.
The saved choice is installed on renderer startup and republished after changes
to trusted Discord windows. This overrides Discord's separate
`useSystemScreensharePicker` preference only when the user chooses an explicit
method. No source IDs, capture callbacks, cancellation handling or active-source
flags are changed by this adapter.

The routing contract was checked against retained current client factory 999926
in `out/native-picker-research/sharing.json`. Factory 725792's
`NativeScreenSharePickerStore` consults this getter to choose its normal picker.
Unknown store shapes are left alone. X11 does not gain a false declaration of
portal support.

## Diagnostics boundary

`integration/video/settings.cjs` supplies normalized preference reads and the
native-side request listener. `integration/updater/screensharing.cjs` restricts
status replies to trusted Discord main frames and a random request ID, coalesces
simultaneous requests, removes listeners after completion and times out after
two seconds. The settings page polls every three seconds only while its tab and
document are visible.

The native transport provides observed capture method, requested and negotiated
codecs separately, encoder implementation, frame rates, bitrate, dimensions,
fallback counts and capture counters. The updater copies an explicit whitelist;
it does not expose application titles, source IDs, users, credentials or complete
native statistics. An absent reply is unavailable, never an inferred idle share.
Missing fields display “Not reported.” A hardware label requires explicit runtime
evidence. The UI does not infer the active capture method from the saved default.

Capture details includes the negotiated producer frame-rate limit. The transport
reports it when capture activity is attributable to the current share and one
installed upstream stream reports a positive negotiated rate. It does not
require the producer to select the requested preference; an applied request
alone never substitutes for missing negotiation data. This is a negotiated
limit, not measured throughput. The actual pacing description says “Skip excess
PipeWire frames before processing to reduce capture work. Applies to your next
screenshare.” The additional initial producer preference and its compatibility
limits are recorded in the [capture implementation notes](capture-implementation-20261102.md#initial-producer-preference-follow-up).

The AV1 compatibility guard records process-wide rejection totals. A separate
camera encoder rejection can affect those totals; observing a screenshare
fallback callback does not prove that the guard rejection belonged to that
share. This limits attribution of the fallback explanation, not the native
compatibility check itself.

Encoding details displays CPU and quality limitation flags as Yes, No or Not
reported. The retained native keys `bwLimitedFrameRate` and
`bwLimitedResolution` represent quality-adaptation/scaler/layer indicators and
do not uniquely identify network congestion. Their labels are therefore
“Quality-limited frame rate” and “Quality-limited resolution”, with a short
explanation that quality limits can reflect encoder quality or bandwidth.
CPU and quality adjustment counts remain separate.

The configured video bitrate limit comes only from the last accepted explicit
`encodingVideoMaxBitRate`; partial updates preserve it, native exceptions leave
it unchanged, and zero/nonpositive or invalid values are unknown. The connection
bandwidth estimate is native `transport.sendBandwidth`, an aggregate connection
congestion-controller target estimate. It is not advertised as bandwidth
available to this video. Diagnostics request filter mask 3 (TRANSPORT | OUTBOUND)
and never copy incoming participant stats or `targetMediaBitrate` into the view.

Rate-limiter, encoder-queue and congestion-window discard counters are the
reported cumulative video-stream totals. Repeated values in rendition stats are
not summed, and they are not converted to FPS or assumed to cover the current
capture-source lifetime. The sanitizer retains only the named numeric fields,
with booleans for flags and nonnegative safe integers for discard counts.
Unknown values remain “Not reported”; outgoing and diagnostic bitrates use
Mbps, kbps or bps so a small nonzero bitrate does not round to zero Mbps.

## Checks

Focused Node tests cover normalization, durable write rollback, unavailable method
rejection, trusted sender/frame/nonce validation, private field exclusion, timeout
cleanup, request coalescing and picker policy behavior. Existing renderer and
settings IPC tests also pass (25 tests in the combined focused run).

The real settings harness has a `DPE_SETTINGS_SCOPE=screensharing` mode. It uses a
fresh profile, real Electron main/preload IPC and synthetic status data, with no
Discord account, call, camera or desktop capture. It covers loading, unavailable,
idle and active states; persistence; failed-save rollback; disabled choices;
keyboard selection; focus through live updates; accessibility names; light/dark
themes; long labels; narrow windows; RTL and zoom.

The first successful UI report is
`out/screensharing-settings-20261102-d/settings.json`. Its settings-only screenshots
revealed selector spacing and sidebar overflow at 150% zoom; those were corrected
with shared layout spacing and navigation text overflow handling. The final UI
report is `out/screensharing-settings-20261102-h/settings.json`, which also asserts
Electron's actual 150% zoom factor so presentation refresh cannot silently undo
that part of the check. A longer method-save operation exposed focus loss when
the combobox was disabled; the page now restores its prior control only when no
other control has taken focus and the panel is still visible. These checks
establish settings behavior, not actual encoder performance or exact upstream
visual parity.

The adaptation follow-up passed the focused real harness at
`out/screensharing-settings-20261102-adaptation-final/settings.json`. It includes
disclosure labels/accessibility names and verifies a nonzero quality adjustment
count alongside false bandwidth flags.

The producer-limit follow-up passed five focused settings unit tests and the
real harness at `out/screensharing-settings-20261102-upstream-limit-b/settings.json`.
It checks a negotiated 60000/1001 limit displayed as 59.9 FPS, separately from a
60 FPS applied request, and “Not reported” when negotiation is unavailable. The
settings-only screenshot was reviewed. This harness used the retained
`4223184105a4…` runtime, with synthetic diagnostics and no live account or capture.

The encoding-diagnostics follow-up passed the real settings harness at
`out/screensharing-settings-20261103-encoding-details/settings.json` using the
same retained runtime. It verifies the configured 6 Mbps limit independently
of a 10 Mbps connection estimate, present/zero/unknown discard totals, the quality
labels and explanation, 10 kbps outgoing bitrate, and an estimate becoming
unknown on refresh. Expanded encoding details also fit at 320 CSS pixels.
Keyboard, save rollback, focus stability, theme, RTL and zoom checks passed;
the saved settings-only screenshot was reviewed. No live account or capture
was used. The follow-up transport and settings Node run passed 18 tests,
including mask 3, accepted/failed/partial options, unknown counters, zero limits
and exclusion of private/raw statistics and `targetMediaBitrate`.

A focused assembled-app check passed with the fresh, logged-out profile at
`out/screenshare-full-candidate-20261102/profile`; its report is
`out/screenshare-production-check-20261102/report.json`. The installed page-world
adapters matched current Discord factories 206959 and 999926 and both reported
`installed: 1`. Loading the native voice module through Discord's existing module
API, with fake audio/video flags, produced no capture or encoder initialization
errors. The helper library was mapped into the renderer. The real DPE settings
window returned `state: "idle"`, methods `auto` and `portal`, and normalized defaults
through its production status IPC, then displayed the idle Screensharing tab.
This check used no account login, call or active capture, and did not inspect
private native hook counters or exercise AV1 encoding. The app was closed after
the bounded check.
