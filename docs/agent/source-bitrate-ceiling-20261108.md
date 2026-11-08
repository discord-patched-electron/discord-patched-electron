# Source-based screenshare bitrate ceilings, 8 November 2026

## Cause and change

Discord's reviewed `getGoliveQuality` divides viewer pixel demand by the selected
encode pixel count. Selecting 2560×1440 while capturing 1920×1080 therefore
scales a 9 Mbps maximum to 5,062,500 bps, even when the viewer requests the entire
available source. The existing source budget only repaired this when **Preserve
bitrate in smaller viewer windows** was enabled.

The default policy now evaluates Discord's maximum against the smaller of the
selected raster and the stable captured source raster. It calls the original
reviewed method through a temporary policy view, then copies only its bitrate
maximum into the original result. The selected encode/capture geometry, minimum,
startup target, original manager and negotiated cap remain intact. A full 1080p
source at a 1440p selection consequently retains the same 9 Mbps maximum as a
1080p selection. Smaller viewer requests still scale the maximum.

Source-size changes refresh both native transport and stream parameters while
the preservation setting is off as well as on. Missing source metadata uses the
original policy. Camera/viewer connections, unknown method bodies and explicit
lower ceilings remain authoritative. The optional smaller-viewer preservation
policy keeps its existing source-budget heuristic. Congestion control continues
to allocate the actual outgoing rate.

## Verification and limits

Added regressions for 1080p sources under 1080p/1440p/4K selections, smaller viewer
demand, source resize/loss, explicit caps, and preserving native geometry and
startup/minimum rates. Tests were not run at the user's request. Repository lint
and whitespace checks passed. The changes are JavaScript helpers supported by
managed watch/restart; no runtime or native rebuild is needed. Live Discord
server allocation and visible quality remain for human verification.
