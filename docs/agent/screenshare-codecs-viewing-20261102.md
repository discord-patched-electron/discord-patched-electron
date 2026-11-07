# Linux screenshare codecs and incoming video

This investigation covers the native media module and renderer negotiation in
the retained Canary client, alongside the capture/cadence work in
[capture implementation](capture-implementation-20261102.md). It does not infer
native Go Live behavior from Chromium's ordinary HTML video playback alone.

## Codec negotiation

Discord negotiates a codec that the sender can encode and every viewer can
decode, and can renegotiate when viewers change. Hardware support does not
justify forcing an encoder after that negotiation. See Discord's
[streaming architecture](https://discord.com/blog/how-it-all-goes-live-an-overview-of-discords-streaming-technology).

The retained renderer module `206959` has two distinct capability checks:

1. `getCodecCapabilities(callback)` returns the native engine's JSON capability
   array. The callback takes one string, not an error-first pair. `AV1X` is
   normalized to `AV1` for signaling and restored for the native encoder.
2. The renderer intersects that array with its signaling flags. AV1 requires
   `signal_av1_encode`/`signal_av1_decode`; H.265 has separate encode/decode
   gates; H.264 and VP8 remain the base codecs. It assigns priorities and RTP
   payload IDs to this intersection before advertising it to the server.

Linux sets AV1 decode signaling but sets encode signaling only when a rollout
experiment is enabled. Therefore a successful AMD AV1 probe can still end in
legacy codec selection before the server sees AV1 as an option. This is a
renderer gate, independent of Chromium's experimental hardware encoder flags.

The follow-up audit confirmed that MediaEngineStore factory `999926` enables
`SIGNAL_AV1_DECODE` unconditionally in its Linux connection branch. Only
`SIGNAL_AV1_ENCODE` is guarded by the rollout result. This applies to both
screenshare and ordinary voice/camera connections; the preceding stream-only
branch configures audio channels, not codec signaling. Native decode capability
still participates in the intersection. Advertising AV1 decoding means a
supported decoder is available, including dav1d fallback; it does not assert
hardware decoding. No extra decode override was added, so deliberate codec
restrictions and decoder-failure handling remain effective. The offline codec
fixture models this upstream decode default and separately checks that native
decode restrictions and hardware preferences remain authoritative.

`integration/video/codecs.cjs` observes the native media-engine module through
the existing shared renderer runtime. It wraps only the exported native engine's
`connect` method and enables AV1 encode signaling on a newly created connection
before its asynchronous capability callback. The original intersection then
does the advertising. AV1 retains its existing preferred ordering; local and
remote capability restrictions, payload IDs and fallback signaling are retained.
The retained renderer's `getCodecParams` returns an empty object for AV1 and
its custom capability report contains only name/encode/decode support. This
work does not add AV1 receiver-level signaling to that protocol or claim a
full SDP level-intersection test. Correct in-bitstream levels and existing SFU
codec intersection are separate requirements.

The adapter rejects unknown/ambiguous module layouts, explicit AV1 denylists,
connections with pending encoder failure, and connections which have already
negotiated codecs. It does not rewrite the final encoder, retry a failed codec,
change the H.264/VP8 fallback, alter congestion control, or modify capabilities
filtered by a client mod. Later explicit flag changes remain effective.

The pinned native `DiscordEncoderFactory::GetSupportedFormats` at `0x443880`
adds AV1 only when one of three GPU encoder capability bits is set. Its
software fallback factories do not add an AV1 encoder. The retained module
contains GPU AV1 VA-API/Vulkan encoders but no libaom encoder implementation.
This supports using the native encode bit for eligibility on this pinned
module; it is not proof that every requested resolution can successfully
initialize an encoder. Native failure callbacks must remain effective.

Native initialization also probes the GPU asynchronously. The synthetic
transport fixture now waits for `getCodecSurvey` before constructing a
connection, since an early connection can retain a software-only factory. A
literal `"null"` survey is valid on Linux and still marks the callback barrier;
it is not a hardware capability report. The fresh capability probe observed
AV1/H.264 encode change from false to true across this barrier; HEVC remained
false and VP8 remained true. GPU discovery took about 12 ms in that probe.

Production already has an asynchronous preparation hook:
`MediaEngineNative.fetchAsyncResources()`, awaited by `RTCConnection` before
voice Identify and native connection construction. The adapter adds the survey
barrier there, preserving synchronous `connect()` and the upstream resources
promise. It waits at most two seconds, permits normal fallback after timeout or
failure, and retries failed preparation on a later connection. Successful
completion is cached per engine. A connection constructed before discovery
cannot be repaired merely by advertising an extra codec.

## Incoming screenshares and cameras

Viewing uses Discord's native receive pipeline and its custom Electron decoder
interface. The shipped integration patch's `DiscordVideoDecoderMediaThread`
creates a decoder through Chromium's GPU factories and initializes it with
`low_delay=true`. The native module wraps this with `PixelCappedDecoderFactory`
and software fallback. This is distinct from the HTML media playback pipeline.

The renderer sets `hardware-h264=1` when Electron video is supported. Despite
its name, the pinned native `ElectronVideoDecoderFactory::Create` checks this
parameter for H.264, AV1 and H.265. Its binary contains no
`hardware-av1-decode` parameter. Adding the separate Windows/macOS AV1 flag to
Linux would therefore not fix this pinned module's decoding path, and no such
flag was added.

The current runtime already has working AMD VA-API AV1/H.264/HEVC decoding in
the separate [codec playback matrix](codec-support.md). No extra Chromium flags
were enabled: forcing the Chromium encoder is unrelated to native Go Live
encoding and has previously crashed on this machine. The private Xvfb native
incoming AV1 test fell back to dav1d even with the GPU device exposed: Chromium
reported video decoding and graphics disabled in that display environment.
The decoder check correctly failed instead of claiming hardware success from
the initial `is_hardware_accelerated=true` factory label.

The explicit Wayland test uses a hidden synthetic window with the host display
socket, while capture, portal, PipeWire and network remain isolated. H.264 at
720p30 passed with 175 unique decoded frames and the actual `electron` decoder.
AV1 at the same aligned size initialized the Electron VA-API path but failed
decoding with `vaapi_video_decoder.cc` code 1 (generic decoding error). This is
an AV1-specific receive failure, not evidence that the GPU was inaccessible.
The current production renderer enables `reset-on-errors=1` and
`fallback-on-consecutive-errors=1`; applying those same parameters in the
fixture recovered 173 unique AV1 frames through dav1d. Delivery passed, while
the explicit hardware decoder assertion correctly failed. These results are
under `out/codec-research-20261102/{h264,av1}-720p30-wayland-debug/`.

No additional hardware-decoding flag is justified by these results. Hardware
support advertisements and a successfully constructed decoder do not prove
that native RTP AV1 streams decode in hardware. A bounded opt-in diagnostic can
retain only this fixture's synthetic AV1 packets with its fixed test key and
reconstruct IVF for independent bitstream validation; it does not access a
real call or account. The reassembly follows the
[WebRTC AV1 depacketizer](https://webrtc.googlesource.com/src/+/main/modules/rtp_rtcp/source/video_rtp_depacketizer_av1.cc)
and [AV1 RTP format](https://aomediacodec.github.io/av1-rtp-spec/).

The isolated transport harness disables Chromium's GPU, so its H.264 receiver
needs Discord's trusted OpenH264 path in the native decoder parameters. This
tests software fallback rather than hardware viewing. Production obtains that
path through its existing verified OpenH264 integration and respects the
user's OpenH264 setting. No new decoder binary was introduced.

Useful native receive observations are `decoderImplementationName`, actual
width/height, `decodeFrameRate`, `renderFrameRate`, decode duration and decoder
error/fallback counters. Send observations include `encoderImplementationName`,
`codecName`, actual size, input/encode rates, media bitrate and target bitrate.
Diagnostics should distinguish these observations from the requested encoder
or capture settings and leave unavailable values unknown.

## Actual sharing options and edge cases

The inspected desktop renderer exposes fixed resolutions 480, 720, 1080 and
1440 plus Source (`0`). Its ordinary FPS choices are 15, 30 and 60. Documents
presets use Source at 15 or 5 FPS. Video presets step through 1440p60,
1080p60, 720p60 and 720p30. Source is how 4K and unusual window dimensions enter
the pipeline; 2160 is not a separate fixed-resolution item in this source.

Source uses `MAINTAIN_FRAMERATE_AND_RESOLUTION` when available, whereas fixed
desktop resolution uses `MAINTAIN_FRAMERATE`. Tests should cover both policies,
monitor/window routing, cancellation/restart, static and moving content,
nonstandard source dimensions and a high refresh source with a lower stream
rate. Codec tests need AV1 hardware success, unsupported/failing AV1 fallback,
H.264 compatibility and a viewer without AV1. A forced local AV1 loopback alone
does not prove server negotiation or mixed-viewer compatibility.

The harness now accepts these fixed sizes, unusual/odd Source dimensions,
5/15/30/60 FPS, independent `--encode-resolution` for a large source downscaled
to fixed quality, and `--quality-mode source`. `--expect-resolution` checks
actual marked decoded frames. The decoder check requires a current
`decoderImplementationName` accompanying decoded frames; a selected factory
that immediately fails does not pass.

Two six-second AMD AV1 runs at 1080p30 reproduced a separate codec padding
issue after the capture dimensions correction: native capture was 1920×1080
with no rendition fallback, but dav1d delivered 1920×1082. Both produced 176
unique marked frames with 100% marker coverage. Software and requested
Electron-decoder modes both failed the explicit 1920×1080 dimension check;
the latter also failed hardware decoder verification. This matches the
pre-VCN5 Mesa AV1 height-padding behavior, and is not a pass merely because
encoding succeeds. The raw reports are under
`out/codec-research-20261102/av1-1080p30-{software,electron}/`.

The native compatibility guard now rejects this actual AMD VCN4 encoder/size
combination before output. A six-second test at 1080p observed one guard
rejection and the genuine native AV1 encoder-fallback callback. The synthetic
server then chose the separately advertised H.264 payload 103 (AV1 remained
101), delivering 173 full-size 1920×1080 frames with AMD VA-API encoding. This
does not relabel AV1 packets or trigger fallback from an unrelated error event.
An aligned 1280×720 AV1 control had zero guard rejections and retained AV1.
Reports are `av1-1080p30-guard-lazy` and `av1-720p30-guard-packets` in the same
output directory.

The aligned control reconstructed 177 complete frames from 638 authenticated
synthetic RTP packets. Both FFmpeg dav1d and FFmpeg VA-API decoded all 177
frames with zero errors. Remuxing the same frames to WebM and playing through
Chromium's ordinary HTML pipeline still produced an initial VA-API decode error
followed by dav1d recovery. This ruled out Discord's RTP transport and custom
decoder configuration. Native hardware-to-software recovery emitted
a codec error but no codec-negotiation fallback event; AV1 software decoding
remained supported. It would be incorrect to claim the receiver negotiated
H.264 in that case.

These decoder comparisons initially used runtime SHA-256
`4223184105a4ee9f162bbfefabbe3a4f0eccc239400d38f88fa56390dd3d945d`.
The staged full candidate uses the newer verified spelling-fix runtime
`c139aa38e2b0f60253d33fc592dd51ec967eca39d222a42c0d39b5dc5e68d1b9`;
the runtime identity must accompany conclusions about current viewing behavior.

The current runtime reproduced the AV1 failure and passed native 1080p AV1
guard-to-H.264 recovery with the actual Electron hardware decoder. The AV1
failure was then traced to the encoder's sequence header: it advertised
`seq_level_idx=0` (level 2.0) for 1280×720, exceeding that level's 147,456-pixel
limit. Chromium's
[libgav1 level-conformance check](https://chromium.googlesource.com/codecs/libgav1/+/refs/heads/main/src/obu_parser.cc)
rejects this; the successful FFmpeg/dav1d runs had tolerated it and therefore
did not establish a conforming stream.

Changing only the three sequence-header level fields in the synthetic recording
from 0 to 5 (level 3.1, suitable for this 720p30 recording) made the current
Chromium HTML pipeline use `VaapiVideoDecoder` with no fallback or decode error.
The control report is
`out/codec-research-20261102/current-native-av1-level31-html/graphics.json`.
This is a diagnostic mutation, not a production header rewrite. Production
needs truthful level selection in the native AV1 sequence-header generator,
including the conditional tier bit at levels above 3.3; weakening Chromium's
validation would retain an invalid stream and hurt other viewers.

The final native constructor hook now selects the level from actual dimensions,
FPS and maximum bitrate before the existing serializer. Real native transport
checks on the current runtime passed with the production transport adapter:

| Requested stream           | Emitted level index | Unique viewer frames in 6 seconds | Actual decoder    |
| -------------------------- | ------------------- | --------------------------------- | ----------------- |
| 1280×720 at 30 FPS         | 5                   | 175                               | Electron hardware |
| 2560×1440 at 60 FPS        | 12                  | 346                               | Electron hardware |
| 3840×2160 Source at 30 FPS | 12                  | 169                               | Electron hardware |

Each retained full requested dimensions, reported AMD VA-API encoding, had zero
codec errors and zero size-guard rejections, and passed active/idle diagnostics.
The recorded 720p RTP independently confirmed level index 5 in the emitted
sequence header. Results are under
`/mnt/storage/dpe-screenshare-final-20261102/validation/av1-{720p30,1440p60,4k30-source}/`.

The final 18-second 1080p60 test combined the native AV1 size guard, negotiated
H.264 fallback, Electron hardware receiving, actual production transport
tracking, a 240 Hz synthetic source, and six seconds of simultaneous CPU/GPU
pressure. All assertions passed. The pressure phase had median 100% CPU across
the 20-worker affinity and 98% GPU utilization, 240 unique full-1080p frames,
and sampled media bitrate 4.91–5.12 Mbps. The adapter admitted 1,080 native
frames from 4,312 callbacks and released 3,232 early through pacing, with zero
CPU rendition fallbacks. Its active snapshot accurately attributed the AMD AV1
fallback, then returned idle and cleared the pacing limit after stopping.

This does not establish a locked 60 FPS: WebRTC's quality adaptation reduced
output to about 40 FPS before pressure began and retained it afterward. No
severe pressure-triggered bitrate collapse appeared in this test, and no
congestion/adaptation algorithm was disabled. The report is
`/mnt/storage/dpe-screenshare-final-20261102/validation/1080p60-guard-transport-pressure/report.json`.

After the final encoder lifetime checks were added, one 12-second 720p
30→10→30 cadence check passed with the exact final helper SHA-256
`e9f545554afa194d4d582a357d32fc8a029aef31d0f93850c1023c67b48d0670`.
It retained Electron hardware decoding and full dimensions in all phases,
reported three corrected sequences at level index 5, had no codec errors or
guard rejections, and passed transport cleanup. Its report is
`/mnt/storage/dpe-screenshare-final-20261102/validation/av1-720p30-final/report.json`.

## Authorized live SFU and picker check

A bounded check in the user's explicitly authorized empty voice channel used
Discord's normal Share Your Screen → Share a window → Make Selection flow and
selected only the dedicated synthetic test window in the GNOME portal. The
client stayed muted and deafened. The stream reached `RTC_CONNECTED` over UDP
and applied the SFU's selected codec, AV1. Its advertised order was AV1
(encode/decode true, priority 1), H.265 (encode false, decode true, priority 2),
H.264 (encode/decode true, priority 3), then VP8 (encode/decode true, priority 4).
This corroborates actual AV1 sending/receiving signaling and server selection,
including the existing Linux decode flag, rather than only a synthetic local
codec choice.

There were zero viewers and no active remote wants. Native statistics reported
30–31 input FPS but zero encoded frames, output FPS and bitrate, with no observed
encoder. DPE correctly reported active PipeWire capture, requested 30 FPS and
hardware status unknown. This proves the normal picker, capture setup and live
SFU negotiation worked; it does not prove live hardware encoding, viewer
delivery, mixed-viewer renegotiation or viewer performance under load. The zero
bitrate is not evidence of the reported slowdown without a requesting viewer.
The earlier direct picker-action attempt omitted the normal dialog flow and
does not establish a product defect.

The share was stopped, the channel was left, mute/deafen and stream preferences
were restored, and the client was closed. No desktop screenshots were saved.
Whitelisted evidence is retained in
`/mnt/storage/dpe-screenshare-final-20261102/live-check/normal-share-flow.json`,
`status.json` and `cleanup.json`.

## Congestion feedback and the remaining live-viewer limit

The synthetic native loopback already exercises transport-wide congestion
feedback. In
`/mnt/storage/dpe-screenshare-adaptation-20261102/scroll-production-pressure-scale/discord-webrtc_0`,
the video sender advertises the transport-wide sequence-number extension at
ID 5 (line 378), and the sender receives transport feedback (line 538 onward).
The native controller processes probes and raises its initial bitrate estimate.
The later 10 Mbps estimate is the configured call ceiling on an unconstrained
localhost path; it does not mean bandwidth estimation is disabled. No extra
renderer RTP-extension option or fabricated receiver estimate is needed.

The retained public renderer separates its bandwidth experiment name into
native client flags and server worker flags. The default is
`bandwidth_estimation/trendline-window-duration-3750,robust-estimator/`;
other treatments add worker pacing, probing or simulcast controls. Only the
middle segment reaches native `setBandwidthEstimationExperiments`. The native
fixture log already confirms the 3,750 ms trendline setting. This audit does
not establish its robust-estimator setting or reproduce an SFU worker's
particular experiment. Server behavior cannot be reproduced by passing its
worker flags to the client.

The missing controlled condition is network pressure: the relay immediately
forwards packets, without a bandwidth cap, queue or propagation delay. Its
all-video-drop negative control tests delivery failure, not a representative
bottleneck. A bounded follow-up should retain the actual receiver's RTCP and
unchanged call/encoder limits, shape only sender-to-receiver traffic through
8→2→8 Mbps phases, add 25 ms propagation delay, and bound the queue to 100 ms.
The expected bitrate reduction during the bottleneck is legitimate adaptation;
the useful checks are recovery, completed size adaptation and continued viewer
delivery after capacity returns. Native feedback logs and outer RTCP
PT 205/FMT 15 counts establish feedback activity without decrypting packets.
An outer-header count can miss feedback embedded later in compound RTCP.

Start that test without CPU pressure: both native endpoints and the Node relay
currently run in one Electron process. Saturating the host can delay the
synthetic receiver and relay as well as the sender, unlike sender-only load
with a remote SFU. Current
[WebRTC congestion-controller source](https://webrtc.googlesource.com/src/+/refs/heads/main/modules/congestion_controller/goog_cc/goog_cc_network_control.cc)
uses packet feedback and receiver estimates in its bitrate decisions; it is
reference behavior, not proof of every detail in Discord's native fork.

An own-account preview is not an independent network viewer. Retained factory
728557 stores one connection per stream key, reuses it on `STREAM_CREATE`, and
derives sender/receiver role from whether the owner is the current user.
`STREAM_WATCH` does not establish a separate receiver for that user's own stream.
Another same-account client is therefore not a demonstrated substitute for an
independent viewer. Discord's
[streaming architecture](https://discord.com/blog/how-it-all-goes-live-an-overview-of-discords-streaming-technology)
also explains why an empty stream does not send encoded video. The authorized
empty-channel check proves SFU codec selection and capture setup; live delivery
and viewer-driven congestion recovery remain unverified without a separate
authorized viewer. Exact public excerpts and the synthetic-log references are
retained in `out/codec-research-20261102/congestion-feedback-audit.json`.

## Small-frame receive decoder policy

The controlled network-loss run later reached 530×298 and changed its receiver
from `electron` to `ffmpeg`. This is intentional size selection, not an error
fallback: `pixel_capped_decoder.cpp` reported `isOver: 0, doFallback: 0`, with no
decoder-error or fallback callbacks. The current native factory at `0x431ed9`
passes a hardcoded `0x38400` (230,400 pixels, 640×360) threshold. On keyframes,
`PixelCappedDecoder::Decode` at `0x447723` selects Electron at or above that
threshold and the low-pixel WebRTC factory below it, when both are available.
686×386 remains above the threshold; 530×298 is below. Decoder labels can update
before the first decoded frame updates the reported dimensions.

Evidence from the running dimension-corrected module is retained in
`out/codec-research-20261102/pixel-capped-decoder-current.txt`. The existing strict
`--decoder electron` check correctly fails an all-Electron assertion when this
switch occurs; delivery and network-shaping results remain separate. Reduced
small-frame overhead is a plausible reason for this policy, not a measured
benefit from this run. No override was added.

## Evidence and validation

Renderer evidence is the retained public asset
`web.8fb853a63180b897.js`, SHA-256
`6c6ce4690146209c83f1683684710d6c921875fe54746e4cc37b80816840a218`,
with extracted module `out/webcam-client-review/206959.js`. A fresh unauthenticated
request to Canary `/app` returned HTTP 403 on 2026-11-02; it was not replaced
with an authenticated request or personal browser data.

Static native disassembly is retained under `out/codec-research-20261102/` for
the pinned candidate native module, SHA-256
`62812b1287d5ea13fd762e91c18a20532734080cd3b30f0059b538281c0c4f64`.
The Electron receive implementation is in the repository's pinned
`patches/discord/44/integration.patch`; it was inspected without changing it.

Eight focused offline regression cases pass in
`tests/updater/video-codecs.test.cjs`, covering capability/remote intersection,
codec exclusions and failure, decoder reporting, module identity/idempotence,
native call/error behavior, and bounded GPU discovery with retry. Nineteen
fixture/packet validation cases pass in `tests/test_screenshare_check.py` and
`tests/test_screenshare_packets.py`, including negative controls for decoder
fallback, altered visible dimensions, authenticated packet reconstruction and
codec-error events that do not trigger encoder fallback. The optional fixture
`--transport-adapter` wraps the native sender with the actual production
transport tracker, delegates pacing to it, and checks observed active media
and cleanup after stopping. Raw native controls remain available for baseline
comparisons.
The observer signature also matches the actual
retained native renderer module. These tests do not establish live SFU codec
selection; the separate authorized live check above establishes that narrower
result. Full repository checks and hardware delivery results are recorded
with the parent screenshare implementation work.

## Follow-up: user codec exclusions

`openasar.screensharing.excludedCodecs` accepts AV1, H265, H264, VP9 and VP8.
The settings menu supports multiple exclusions and leaves at least one listed
codec allowed; IPC independently rejects excluding all five. Native capabilities
remain authoritative, so leaving an unavailable codec selected does not invent an
encoder. If no supported encoder survives, the outgoing capability list retains
no encodable video codec and Screensharing reports that another codec must be
allowed. An excluded codec is never silently restored as a compatibility fallback.

The updated public renderer asset retained at
`out/optimized-picker-research/web.d01f79ca6dd8c64a.js` confirms the native
connection still assigns `codecs` before advertising to the SFU, rebuilds it from
`initialCodecs` when native overrides change, and reduces it after encoder failure.
The adapter intercepts those two per-connection list assignments before the first
asynchronous capability callback. It only clears `encode` on excluded video
entries, preserving decoder flags, payload IDs, priorities and native fallbacks.
Scope is `context === "stream" && streamUserId === userId`; camera calls and
received streams do not receive the exclusion adapter. Changes are copied into
new outgoing connections, leaving an existing negotiated share alone.

The UI is in Screensharing alongside the two audio preferences moved from General.
Regression fixtures were extended for multi-selection, pending authorization,
revocation, native-capability exhaustion, incoming/camera isolation and preference
updates. Validation of this follow-up is deferred to the coordinated final pass;
results from the earlier section do not describe these new cases.
