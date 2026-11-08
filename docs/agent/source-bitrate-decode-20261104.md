# Live source budgeting and receive decoder diagnostics

Follow-up, 5 November: the viewer-bitrate override below is now opt-in through
DPE Settings > Screensharing > Quality > **Preserve bitrate in smaller viewer
windows** (`preserveViewerBitrate`, default false). Off returns the reviewed
native quality policy unchanged, including viewer-size/zoom demand. On restores
the source/selected-quality budget described below. Missing or non-boolean values
remain off. Startup and saved-setting application both propagate the option;
existing manager wrappers consult the current value. Source metadata remains
available while off, but source events do not force quality refreshes. Codec
exclusions, the separate environment bitrate experiment, and the later packet
pacing/receiver-clock corrections retain their existing behaviour.

Validation uses the real isolated settings harness for default-off, keyboard
toggle, saved/reloaded states, disk-write rollback, and long labels at narrow
width/150% zoom. Native-policy tests cover reduced viewer demand and increased
zoom-equivalent pixel demand while off, plus the optional source budget while on.
Artifacts: `out/viewer-bitrate-option-20261105/`.

The user's follow-up confirmed good motion quality at approximately 9 Mbps, but
reported scene-change judder on all codecs. The current manual logs still contain
bitrate-limiter drops. They do not establish that every visible stall has the
same cause, or that increasing the advertised ceiling removes scene-cut stalls.

## Source-aware ceiling

The existing PipeWire Format listener now records the live negotiated width and
height independently of whether the producer supports variable FPS. Fixed-rate
producers are valid dimension sources too. Geometry is exposed only with one
active PipeWire capture stream, cleared at teardown, and never inferred from a
previous encoder's output resolution. Unit tests exercise a 1080p-to-720p Format
change. No additional GPU pass, CPU readback or PipeWire renegotiation is added.

For one active outgoing share, a lightweight 500 ms timer reads that existing
metadata. Two matching observations settle a size before publishing an anonymous
size-only event. Source loss clears it immediately. The timer stops with the
share. A changed stable size reapplies the selected quality using Discord's
original connection method; it does not permanently rewrite the user's choice.
A 1440p selection can therefore remain an upper bound while a 1080p window stays
1080p, and later grow again. Capture and encoded dimensions are displayed
separately in DPE Settings.

The quality-manager adapter budgets the minimum of the selected raster and current
source raster. Its initial heuristic is
`9 Mbps * sqrt(pixels / 2073600) * sqrt(FPS / 60)`, rounded up to 250 kbps steps,
bounded by the application's original negotiated maximum and minimum. Thus the
known-good 1080p60 budget stays intact; smaller source windows can use smaller
ceilings. Viewer tile size does not lower this ceiling because the reviewed
native policy keeps the full encode raster. The later
[receiver investigation](receiver-clock-av1-padding-20261104.md) caught and fixed
that mismatch and a native no-op in source-size refreshes. This is a documented
heuristic, not a recovered Windows formula or a proof of perceptual optimality.
Content-dependent actual rate and congestion
control remain native. Explicit smaller limits and unknown native policy bodies
remain authoritative. With no reliable source dimensions, the existing
negotiated-ceiling repair remains the fallback.

The manual sender no longer supplies 32 Mbps by default. The explicit environment
option remains available for separate ceiling experiments. No hardcoded minimum
outgoing bitrate, filler stream or network-cap bypass was added.

## Incoming video

DPE Settings > Screensharing now shows anonymous incoming video diagnostics:
codec, active decoder, hardware/software status, decoded size and frame rate.
These cover incoming video, including camera video, rather than guessing that
every received track is a screenshare. No account IDs, SSRCs, titles, addresses,
keys or raw native reports cross the settings bridge. Empty/undecoded streams
are hidden and destroyed connections cannot publish late replies.

The reviewed native GetDecoderInfo methods declare hardware=true for Electron
and false for dav1d/libvpx. Scope generation checks the actual constant flag store
at byte 0x18 in each implementation and ties the metadata to the complete module
hash. Hardware labels use that declaration with the currently reported decoder
name; they do not infer hardware use from GPU feature availability. Unknown
implementations remain "Not reported". This reports the native decoder contract,
not independent GPU engine-utilization measurement. Software fallback updates
are reflected by the next settings poll.

## Scene-cut experiments and remaining gap

`out/dynamic-quality-20261104/` retains matched controls and rejected experiments.
New synthetic patterns alternate quiet frames and either detailed moving texture
(`cuts`) or full-frame random noise (`scenes`) every 120 producer frames. Tests
can constrain the synthetic receiver/call allowance separately from the stream
ceiling. An initial receiver-report-only experiment did not constrain native
allocation, so it must not be cited as a 9 Mbps allocation test.

A tighter native peak equal to the current allocation was tried for AMD H.264
and the private HEVC/AV1 backend. It did not safely fix cadence: the matched H.264
texture-cut run acquired extra limiter drops. The native peak changes and their
specialized tests were removed; the existing proven rate-control tuning remains.
This is consistent with the earlier rejected Mesa CBR experiment in the
[Windows parity audit](windows-encoder-parity-20261104.md). Do not re-enable either
merely because the rate-control name resembles Windows.

The final unchanged-encoder, 9 Mbps tests passed delivery for H.264, HEVC, AV1 and
VP8, verified the actual 1920x1080 capture size, and reported hardware decoding
for HEVC/AV1 and software decoding for the selected H.264/VP8 receiver paths.
HEVC/AV1/VP8 had no sampled rate-limiter drops in these final runs. H.264 still
recorded 14. Passing delivery is not proof of smooth cadence at every cut or of
Windows parity. Residual scene-change judder remains unresolved; no global
frame-dropper disable or fake trusted-controller flag was shipped.

The real settings harness passed under private Xvfb, including hardware/software
receive states, empty state, keyboard, save rollback, narrow layout and existing
diagnostics. Screenshots depict only that isolated synthetic settings UI.

Final checks: lint passed; 696 Python tests and 422 JavaScript tests passed
(three optional retained-source tests skipped). The retained-source bitrate,
transport and decoder-contract suites passed all 32 tests explicitly. The
experimental sender and viewer share the atomically updated helper/UI payload.
Restart both to load it; no running logged-in instance was restarted.
