# Receiver pixel requests and motion bitrate, 4 November 2026

The manual sender's two rotated RTC logs reproduce the reported reduction.
The application and effective stream ceilings fall from 9,000,000 to
4,423,680–5,017,698 bps, while the receiver still advertises 9,000,000 bps,
receiver loss is zero, and transport headroom remains available. These are
application configuration changes, rather than merely an efficient encoder
producing fewer bytes. Sanitized observations are in
`out/bitrate-headroom-20261104/live-sanitized.json`; raw account logs were not copied.

Discord's retained `getGoliveQuality` scales its ceiling with receiver pixel
wants. For 2560×1440 at 60 FPS its floor is
`0.02 * 2560 * 1440 * 60 = 4,423,680`, exactly matching the live reconfiguration.
That is separate from the earlier native 4 Mbps untrusted-QP table correction.
The native correction remains necessary and unchanged.

`integration/video/bitrate.cjs` now preserves the negotiated high-quality share
ceiling through that reviewed policy. It keeps receiver pixel/FPS requests,
capture geometry, scaled startup target/minimum, native congestion control,
explicit smaller application ceilings and quality overwrites. It applies only
to the sending Go Live connection; camera and viewer connections stay native.
Unknown quality-manager bodies are left alone. The repair runs without any
higher-ceiling environment option.

The optional ceiling experiment now includes VP8. The manual sender launcher
requests 32 Mbps by default through `DPE_SCREENSHARE_MAX_BITRATE`, which can be
overridden. This is an experiment ceiling, not a discovered universal Discord
server limit. It uses both native call headroom and the existing stream Video
signaling; receiver/server feedback still limits the allocation. The regular
application retains its existing default unless the option is supplied.

The VP8 live sample had 60–61 FPS input but 24–34 FPS output during motion,
without CPU-limited flags, at the same reduced ceiling. Its bitrate starvation
must be addressed before blaming libvpx CPU speed. The
[upstream VP8 implementation](https://chromium.googlesource.com/external/webrtc/+/master/modules/video_coding/codecs/vp8/libvpx_vp8_encoder.cc)
has separate real-time speed and temporal-layer controls; no unreviewed native
speed patch or CPU-adaptation disable was added here.

VP9 is absent from this native build's real encode/decode capability list.
The exclusion error correctly prevents silently restoring an excluded H.264
encoder. Installing a standalone VP9 library does not add a Discord encoder
factory. This does not establish that every Discord platform lacks VP9, so no
global capability blacklist was added.

## Validation

The JS tests execute the retained production quality-manager body and reproduce
the exact 4,423,680 bps result, then verify the corrected 9 Mbps result without
changing the scaled target. Other tests cover explicit smaller policies,
camera/viewer isolation and signaling/native ceiling agreement.

Fresh synthetic PipeWire DMA-BUF sender/receiver runs reconfigure after 2.5
seconds and use the already verified runtime/helper. All four codecs passed
local delivery at a configured 32 Mbps ceiling and 1080p60 source (AV1 output
remains the documented 1920×1072 resample). AV1 reached approximately 31 Mbps.
VP8 reached 58–61 FPS after its startup interval. Every sampled rate-limiter
discard counter in these four runs was zero. H.264 and HEVC used less on the
scrolling test pattern. These runs establish local delivery and headroom,
not live SFU acceptance or Windows visual-quality parity.

Reports: `out/bitrate-headroom-20261104/*-headroom/report.json`.
The first matrix intentionally remains as failed evidence: H.264 lacked its
OpenH264 receiver asset, and VP8 correctly reported libvpx instead of the
requested Electron decoder. Both were rerun with the proper decoder selection.
The fixture's production call maximum now includes the selected ceiling plus
its 1 Mbps margin, preserving the original 10 Mbps floor.

A worst-case H.264 noise control distinguishes underuse from a real cap: the
9 Mbps run sampled 23–39 FPS with 189 rate-limiter discards by the final sample;
the 32 Mbps run sampled 57–61 FPS, zero such discards and up to 31.1 Mbps of
reported media. Noise is intentionally unrepresentative of a normal desktop,
and reported short-window RTP media is not exact encoder payload throughput.
Reports: `h264-noise-{9000000,32000000}/report.json` in the same artifact directory.

Checks: lint passed; 696 Python tests passed; 415 JavaScript tests passed with
three optional retained-source tests skipped. Running the retained-source
bitrate/codec suites explicitly passed all 24 tests, with no skips.

HEVC's separate noise run also passed, reaching 60–61 FPS after startup with
zero sampled rate-limiter drops and approximately 33 Mbps reported RTP media
at a 32 Mbps encoder ceiling. RTP media includes reporting/packet overhead;
it is not an exact elementary-stream bitrate.

`acceptance.json` records nine passing regression gates and one unverified gate:
live Discord server acceptance of 32 Mbps. This gate count is not a visual
quality score or proof of Windows parity. No account call was initiated, and
running manual instances were left untouched for the user's test.
