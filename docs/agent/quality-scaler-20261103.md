# Screenshare quality adaptation investigation, 2026-11-03

Historical preset-only evidence follows. The subsequent
[allocation-aware correction](encoder-allocation-20261103.md) supersedes the
candidate and unresolved adaptation conclusion below, while retaining the
exact-framing AV1 guard and live-validation limitations.

The current user's 1486×836/60 FPS screenshot is consistent with an observed
1080p quality-scaler step, not an encoder throughput measurement. The current
candidate's native log shows the matching sequence at 00:21:43–00:22:27:

- Hardware H.264 VAAPI after the AMD VCN4 AV1 dimension guard rejected 1080p.
- QP threshold 24/37. The sampled QP rises to 42, then
  `QualityScalerResource kOveruse` requests 1,244,160 pixels (3/5 of 1080p).
- GPU output follows 1920×1080 → 1486×836 → 1148×646 → 888×500, then recovers
  to 1080p as the content becomes easier. Output stays around 60 FPS.
- CPU limitation is false. Receiver REMB is 9 Mbps, reported loss is zero and
  RTT is roughly 5–6 ms. Encoder targets around 7.3 Mbps remain present during
  the downscales. This is not evidence of a network bandwidth collapse.
- Keyframe count changes alongside resize. This sequence does not demonstrate
  the repeated FPS-update reset issue described in Discord's AMD article.

No account identifiers, source names or full live log are copied here. This
establishes the cause of this particular observed downscale, not every reported
low-bitrate incident.

## Current AV1 hardware limitation

A bounded synthetic FFmpeg VAAPI encode of 30 frames at 1920×1080 on the current
RX 7900 GRE/Mesa 26.2.5 still decodes as 1920×1082. The existing guard is not
stale. GPU Screen Recorder's retained source explicitly describes this AMD
special case. A metadata-only dimensions change cannot fix the decoded image.
The user explicitly chose exact framing over an aligned frame with black bars,
so the AV1 guard remains. No padding, cropping, stretching or fake negotiated
codec was added.

Artifacts: `out/quality-regression/av1-1080.ivf` (generated test pattern only),
`ffmpeg-av1.log`. Both ffprobe and software decode completed successfully, but
the actual height is wrong for the requested 1080.

## AMD VAAPI preset

A private native sender with `LIBVA_TRACE` establishes that Discord already uses
H.264 High, CABAC and 8×8 transforms. It sends `quality_level=0`. The AMD-authored
[Mesa 26.2.5 frontend](https://github.com/chaotic-cx/mesa-mirror/blob/mesa-26.2.5/src/gallium/frontends/va/encode.c)
explicitly interprets zero as **speed**, with VBAQ and pre-encode disabled.
`0x13` requests balanced motion search and VBAQ, without pre-encode. This is a
Mesa AMD packed extension, not the generic VAAPI quality-level ordering.

The encoder helper now intercepts the reviewed native `create_tunings` linkage
and changes only the present/default H.264 quality value for a VA display whose
vendor identifies Mesa Gallium AMD/ATI. It preserves bitrate, frame rate, QP
limits, HRD, keyframe controls and congestion/quality adaptation. Other codecs,
other vendors/drivers and non-default presets are unchanged. No frame copies,
CPU analysis, pre-encode analysis or lookahead are introduced. Balanced motion
search can cost more work inside the hardware encoder than speed mode.

The scope generator binds the eagerly resolved slot and vendor-query target to
reviewed complete native module hashes. Installation verifies linkage,
executable targets, construction context and the native module identity before
mutation. It runs before voice initialization and restores protection. Runtime
stats report whether installed and how many tuning updates were corrected.

## Diagnostics and fixture corrections

`transport.cjs` now derives average quantizer from interval QP/frame deltas,
not the lifetime average. It discards baselines on source, codec, dimensions,
encoder and counter changes; stale responses cannot replace a newer baseline.
Values are bounded by each codec's actual QP range. Keyframe counts come from
the actual media rendition's `frameCounts.keyFrames`, not an invented top-level
field. Missing counters stay unknown.

The production-option fixture now sends the current stream experiment string,
including `nvRelaxRc=250`, a 100-second GOP matching native 6,000 frames at
60 FPS, and `alwaysSendVideo: false`. The old forced-sending flag selected native
**High** latency, compared with the user's **Medium**. The experiment string
alone does not select Medium. The old 3-second GOP also added artificial resets.
The private relay now sends authenticated synthetic SFU receiver announcements
so forced sending is unnecessary. These are fixture corrections, not changes to
production stream options.

Initial preset experiments are retained, including failures, under
`out/quality-regression/`:

- `speed-control` / `balanced-vbaq`: native sender and OpenH264 delivery pass;
  LIBVA trace confirms actual quality 0 versus 19. The 4-pixel random texture
  saturates QP at 50–51. Both downscale to 1148×646. No improvement established.
- `detail-speed-control` / `detail-balanced-vbaq`: the owned scrolling texture
  has 16-pixel blocks. Sender cadence stays 60 FPS and final interval QP changes
  from about 39 to 34, but both still downscale. **Receiver validation fails**:
  the isolated X11 runtime selects unavailable FFmpeg H.264 decoding despite
  requesting the Electron decoder. These are encoder observations only.
- The OpenH264 runs also expose native `avcodec_receive_frame=-11` (EAGAIN) being
  treated as an error at the first frame/resize. This produces unnecessary PLI
  requests. The separate decoder investigation owns its correction. H.264 VA
  sequence parameters use level 5.2 with correct 1080 cropping, not an
  underspecified 1080p60 level.

These early experiments used the old fixture latency/GOP and do not establish
that the overall low-quality report is fixed. Do not present a preset change,
frame-delivery pass or unsupported receiver as that proof.

References:

- [Discord's AMD encoding investigation](https://discord.com/blog/from-blocky-to-brilliant-improving-video-quality-on-discord-go-live-on-amd-gpus)
- [VAAPI H.264 backend](https://github.com/chromeos/cros-codecs/blob/main/src/encoder/stateless/h264/vaapi.rs)
- [Existing native statistics audit](screenshare-native-gpu-20261102.md#native-statistics-audit-estimate-configured-limit-and-actual-output)

## Application ceiling

The retained Discord frontend's `setDesktopEncodingOptions(width, height, fps)`
uses a `setCalcMaxBitrateFunc` callback. Its returned ceiling updates the native
transport and the video event sent as opcode 12 `streams[].max_bitrate`. Current
callbacks include guild/user experiment restrictions and a 9 Mbps ceiling for
ordinary higher-resolution/frame-rate shares. The observed 9 Mbps REMB is not a
measurement proving the network only supports 9 Mbps. No unconditional ceiling
raise was applied: whether the SFU accepts a larger allocation is unverified.

The official [AMD hardware issue](https://github.com/GPUOpen-LibrariesAndSDKs/AMF/issues/423)
and [AMF AV1 API notes](https://github.com/GPUOpen-LibrariesAndSDKs/AMF/wiki/AV1-Encoder#av1-specific-api)
corroborate the RX 7000 AV1 coded-height restriction. This hardware result must
not be generalized to every later VCN revision or API implementation.

## Final bounded comparison and conclusion

The first Medium-latency attempt (`final2-*`) encoded no frames because its
private relay lacked SFU receiver-count signaling. A preceding attempt failed
before native initialization due to an isolated missing `libmediapipe.so`.
These failed artifacts are retained and are not counted as validation.

Static review found the exact missing message: authenticated RTCP APP, name
`0x30534944` (network-order bytes `0SID`), video SSRC 44, subtype/receiver count 1.
The 12-byte report block contains synthetic user 2000, a native-compatible
18-bit-mantissa/6-bit-exponent bitrate and flags. It uses the existing fixed
fixture key, 8-byte RTCP associated data and unique nonces outside the peers'
bounded ranges. System libsodium performs authentication, with no new Python
package. A 2-second check verified native receiver-count acceptance and Medium
latency before the final pair. Unit tests authenticate/decode the fields and
reject header tampering. No real account or network credentials are involved.

The final `sfu-speed-control` / `sfu-balanced-vbaq` pair uses exact 1920×1080,
60 FPS, 9 Mbps receiver allocation, 100-second GOP, Medium latency, the owned
16-pixel-block scrolling scene and the corrected native EAGAIN decoder.
Both 12-second runs pass all native sender/receiver validation gates:

| Measurement                      | Original speed | Balanced + VBAQ |
| -------------------------------- | -------------: | --------------: |
| Encoded frames                   |            690 |             701 |
| Distinct decoded marker frames   |            639 |             660 |
| Final interval QP                |          38.23 |           33.22 |
| Final reported encoder FPS       |             57 |              60 |
| Final output size                |       1148×646 |        1148×646 |
| Keyframes                        |              6 |               6 |
| Native receive-EAGAIN errors     |              0 |               0 |
| Source-to-native-receiver median |         156 ms |          143 ms |
| Source-to-native-receiver p95    |         469 ms |          389 ms |

Both follow 1920×1080 → 1486×836 → 1148×646. The preset has a measured full-chain
QP improvement without a throughput regression in this bounded comparison, but
**does not prevent the resolution reductions**. QP is the native codec metric,
not an objective visual-quality score. One matched pair is not a latency
confidence interval, and these receiver callbacks include software decode/output
conversion. Native adaptation, SFU allocation and exact framing remain intact.
A live SFU/user-scene comparison remains needed for the reported experience.

Validation: 44 focused Python screenshare tests pass after the final fixture
correction, in addition to the earlier encoder/transport checks. Parent owns
final lint, staging and the full-suite result below. The helper remains
`out/quality-regression/production-confirm/helper-build/libdpe-camera-probe.so`;
no encoder C changes followed that build. The newly required `amd_quality`
metadata must be regenerated for the prepared native module during staging.

## Parent integration and candidate

The parent staged the changes into
`/mnt/storage/dpe-kms-20261102/candidate`, including the updated helper, all
native patch/scope scripts, cursor helpers, portable monitor ordering and
compiled settings UI. Capture-bundle and distribution validation passed; the
Electron executable is unchanged. Normal next startup regenerates the immutable
native module generation and scopes. No running client was restarted.

Validation: 675 Python tests passed; JavaScript passed 400 with one skipped.
The final fixture adjustment additionally passed 44 focused tests. Final lint
passed. The real fresh-profile Screensharing settings harness passed, including
optimistic disabled game controls, requested security wording and new QP/keyframe
diagnostics: `/mnt/storage/dpe-quality-20261103/settings-diagnostics/settings.json`.

Candidate verification explicitly records that the main low-bitrate/quality
issue is **not proven fixed**. Do not close that goal on these side fixes. The faithful synthetic comparison
now passes but retains the resolution reductions. Actual SFU acceptance of a
higher application ceiling and the user’s scene remain unverified. Do not run unattended account calls to obtain it. Exact 1080p remains
required; the user rejected the AV1 aligned-frame workaround.
