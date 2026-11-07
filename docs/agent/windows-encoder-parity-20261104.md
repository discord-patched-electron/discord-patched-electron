# Windows encoder parity investigation, 4 November 2026

**The requested Windows parity is not established.** No Windows runtime or live
Discord call was tested. The working tree now has real GPU HEVC encoding,
filtered capture chroma and a full-picture GPU AV1 alignment workaround. See
[the current implementation and validation](hevc-av1-chroma-20261104.md).
AV1 uses 1920×1072 for a 1920×1080 source; it does not provide pixel-exact 1080p.

The user's latest instruction rejects visible AV1 padding and card blacklists,
superseding the earlier permission to pad. The padded/header-rewrite experiments
below are historical failures. The new implementation keeps the whole picture
through GPU resampling, without padded rows. No Electron rebuild, account login,
unattended call or physical camera/desktop recording was performed. Saved video
contains only the fixture's generated pixels.

## Inputs and repeatable audit

- Windows Canary host 1.0.1292 native module, downloaded in the preceding audit:
  `out/vp9-windows/discord_voice.node`, SHA-256
  `6803596ec541909fa71a502d71dcf7d95b5c62f4cbb02d0d09bfbecaef4d7f3b`.
- Prepared Linux module: `out/quality-regression/final-input/discord_voice.node`,
  SHA-256 `83f278107c08b7717ce9f9c147ee5e161049f1626773d054d514ce98e9223977`.
- Reused Electron executable:
  `/mnt/storage/dpe-minecraft-20261102/confirmed-current/runtime/electron`,
  SHA-256 `c139aa38e2b0f60253d33fc592dd51ec967eca39d222a42c0d39b5dc5e68d1b9`.
- RX 7900 GRE, Mesa Gallium 26.2.5, render node `/dev/dri/renderD128`.

```sh
uv run python scripts/audit_windows_encoder.py \
  out/vp9-windows/discord_voice.node \
  --output out/encoder-parity/windows-audit.json
```

This bounded PE64/unwind-table audit preserves property references and complete
small functions, including conditional branches. It explicitly reports that
runtime choices and effective driver settings are unverified. Property strings
alone are insufficient evidence of values or of a selected encoder. The audit
does not execute the proprietary Windows binary.

## Windows behavior and Linux equivalents

Discord documents window capture through Windows Graphics Capture and an
injected capture DLL, with fallback for unsupported cases. DXGI duplication
returns GPU surfaces plus damage/move/cursor metadata. These are capture
interfaces; neither guarantees better compressed pictures. Linux's existing
portal/DMA-BUF, direct Wayland and optional injected game paths address the
corresponding capture stages, but require their own frame-age and load checks.
[Discord capture documentation][capture], [Microsoft DXGI documentation][dxgi]

The Windows module contains separate AMF H.264, HEVC and AV1 configuration
functions, NVENC paths and Windows Media Foundation support. The Linux module's
VA-API backend implements H.264 and AV1, with a missing HEVC construction path.
Changing Chromium flags or a capability label does not implement that backend.
The [earlier HEVC ABI audit](hevc-native-gap-20261103.md) documents the dispatch
gap. Media Foundation configuration is driver/platform-specific; finding AMF
defaults does not prove every AMD Windows share uses AMF.

The recovered Windows AMF H.264 configuration at RVA `0x632480` requests:

| Property                            | Static value or branch                                                                                           |
| ----------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| Usage                               | 0, then explicit properties below                                                                                |
| Profile / level                     | High (100), level 5.2 (52)                                                                                       |
| RateControlMethod                   | CBR (1), except Unlimited latency selects HQ VBR (5)                                                             |
| MinQP                               | 1                                                                                                                |
| EnforceHRD                          | Disabled for High/Unlimited latency; enabled for the other reviewed latency values                               |
| BPicturesPattern / BReferenceEnable | 0 / false                                                                                                        |
| MaxNumRefFrames                     | 16 requested; effective hardware count unverified                                                                |
| EnableVBAQ                          | true                                                                                                             |
| RateControlPreanalysisEnable        | Enabled except UltraLow latency                                                                                  |
| QualityPreset                       | Speed (1) at FPS ≥60 and height ≥1080 with ordinary latency; Quality (2) below either threshold or for Unlimited |
| LowLatencyInternal                  | Explicitly enabled in the UltraLow branch                                                                        |
| FrameRate                           | Session FPS / 1                                                                                                  |
| FillerDataEnable                    | false                                                                                                            |

The quality branch is easy to reverse accidentally: the comparisons use
`setb`, combine the **below-threshold** conditions, then increment the result.
Value 1 means Speed; 2 means Quality in the [AMF enum definitions][amf-h264].
The properties still pass through setters, experiments, capability handling and
driver validation. These are recovered application requests, not a Windows
quality benchmark or proof the driver accepts sixteen active references.

Windows's `VBVBufferSize` and bitrate setters take dynamic caller arguments.
No single fixed VBV size is inferred from their property names. Discord's
[AMD investigation][amd-blog] identifies unnecessary resets during FPS changes
and rate-control/keyframe interactions as serious quality failures. The current
Linux reconfiguration fixture exercises ordinary repeated transport updates;
the successful H.264 runs do not show a keyframe on every FPS update.

NVIDIA's APIs support both Windows and Linux. Completion handling differs;
Windows asynchronous events are not a Linux encoder setting. Preserve native
NVENC's platform implementation and device capability checks. Intel VA-API is
also a distinct implementation, not QSV enabled by an enum change. The
[NVIDIA](screenshare-nvidia-20261102.md) and
[Intel](screenshare-intel-20261102.md) source audits remain relevant, with their
explicit lack of hardware measurements. No new blanket vendor tuning was made.

The expanded audit also retains complete AMF HEVC and AV1 setup functions at
RVAs `0x636170` and `0x631b90`. Their relevant requests are:

| Setting               | Windows HEVC                                              | Windows AV1                                                                                                       |
| --------------------- | --------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| Profile               | Main (1), Main tier (0)                                   | Main (1)                                                                                                          |
| Rate control          | CBR (3); latency field 3 selects peak-constrained VBR (2) | Peak-constrained VBR (2)                                                                                          |
| Quality               | Balanced (5); latency field 4 selects Speed (10)          | 70; latency field 4 selects 100                                                                                   |
| Minimum QP            | I and P explicitly 1                                      | No corresponding setter established by this audit                                                                 |
| Adaptive quantization | VBAQ enabled                                              | No corresponding setter established by this audit                                                                 |
| Preanalysis           | Enabled unless latency field is 4                         | No corresponding setter established by this audit                                                                 |
| Frame rate            | Dynamic session FPS / 1                                   | Dynamic session FPS / 1                                                                                           |
| Other behavior        | Filler disabled; reference count comes from a helper      | Query timeout 0; frame skipping disabled for latency field 4; alignment capability queries and conditional mode 3 |

These numeric branches are recorded independently for each codec. The larger
HEVC reference-count helper and AV1 alignment capability results are not replaced
with guessed constants. Enum definitions come from the
[HEVC AMF header](https://github.com/GPUOpen-LibrariesAndSDKs/AMF/blob/master/amf/public/include/components/VideoEncoderHEVC.h)
and [AV1 AMF header](https://github.com/GPUOpen-LibrariesAndSDKs/AMF/blob/master/amf/public/include/components/VideoEncoderAV1.h).
The current Linux backend supplies Main/low-delay coding, AQ, dynamic RC/HRD/FPS
and proper receiver delivery through VA-API. Its VBR control and Mesa quality
encoding are different APIs; the Windows numeric preset values cannot be copied
into them. Effective Windows driver choices remain unmeasured.

Expanded evidence: `out/av1-alignment/windows-expanded-audit.json`. Reproduce it
with the audit command above. Source rates, levels and VBV remain dynamic
inputs; neither this static audit nor the Linux encoder's capacity establishes
an authenticated Discord allocation ceiling.

## Bitrate: default, allocation and output are different

The retained shared frontend has a 9,000,000 bps higher-quality default,
3,500,000 bps lower default and separate 10,000,000 bps call maximum. Its
maximum callback depends on account/guild experiments and other policy. The
same frontend is used across platforms; no Windows-only universal higher
ceiling was established. These are client policy values, **not proof of a
universal SFU or hardware limit**.

The existing opt-in ceiling experiment preserves explicit lower callback
results and leaves ordinary launches unchanged. A preceding local 16 Mbps
fixture establishes native capacity above 9 Mbps, not server acceptance. See
[the bitrate experiment](screenshare-bitrate-experiment-20261103.md). No further
ceiling increase was applied in this investigation.

The actual allocation changes with receiver signaling, congestion, overshoot,
stream limits and adaptation. Counter names such as `targetMediaBitrate` must
not be assumed to expose a configured ceiling: in these reports they can match
actual transmitted output. Native encode options and authenticated receiver
reports are separate evidence. A real SFU grant and Windows/client policy
comparison remain unmeasured; an unattended logged-in call was not substituted
for that missing evidence.

## H.264 compression experiments

`out/encoder-parity/` retains scripts, native traces, private profiles and failed
results. Standalone FFmpeg comparisons used the same six-second 1080p60
`testsrc2`, High profile, no B-frames, one reference and long GOP, with a
7.5 Mbps target, 9 Mbps peak and target-sized VBV. VMAF compares the exact
generated source frames; these are not Minecraft or Windows measurements.

- Mesa's packed quality 19 (balanced + VBAQ) remains the production choice.
  Quality 21/23 produced a small standalone gain but no matched native-source
  improvement that justified the additional encoder work.
- Pre-encode variants improved the initial VMAF measurement while substantially
  overshooting the requested rate. Lower-target runs brought their measured
  bitrate closer to the baseline and removed the apparent substantial advantage.
  They also reduce encoder capacity and add a pass. They were not enabled.
- CBR is closer to Windows's interactive AMF request, but is not automatically
  better on Mesa. Standalone output overshot its target. The native CBR/VBAQ
  experiment retained exact framing but had roughly 503 ms median latency and
  worse cadence in its private run. It was rejected, not copied into production.
- The H.264 wire headers show High/CABAC and one reference. They show 8×8
  transforms disabled. **Discord already requests 8×8 transforms** in the
  actual VA-API picture parameters. Mesa 26.2.5's
  [`ac_video.c`][mesa-caps] enables this hardware capability only at VCN5 and
  later; the driver masks it on this VCN4 card. The private PPS toggle was a
  no-op, not a compression fix.
- The actual VA-API capability query reports H.264 L0=2/L1=1. The current native
  low-delay predictor uses one reference. Windows requests a larger maximum,
  but its active count is unmeasured. A standalone `-refs 2` attempt emitted
  identical output and did not demonstrate a two-reference P-frame structure.
  Merely raising an SPS reference count would not implement DPB ownership,
  predictor synchronization and matching slice reference lists. No unsafe
  metadata-only reference change was applied.

`scripts/extract_screenshare_h264.py` adds fixed-key synthetic RTP
depacketization with complete-fragment checks. The source can log only its
timestamp/sequence map when packet recording is explicitly enabled.
`scripts/analyze_encoder_quality.py` matches decoded CRC markers to that map and
reconstructs exact shader source samples. Its sampled RGB PSNR includes color
conversion and chroma subsampling; it is not VMAF. Per-frame source sequence and
error are retained for subsequent comparisons on the common sequence set.
It reports exact source framing separately, so padding cannot disappear inside
an average quality score.

## AV1: verified failure of sender-only padding/cropping candidates

The AMD issue is also documented for Windows; it is not evidence that Linux
simply lacks a working AMF option. AMD describes the pre-VCN5 geometry
restriction and the 1080-line special case.
[AMD issue 423][av1-issue], [AMF AV1 API][amf-av1]

Experiments on this card:

1. Requesting unaligned 1920×1080 still produces the known wrong coded height.
2. Native aligned 1920×1088 encoding preserves the full source picture but
   exposes padding. It passed delivery and failed the user's acceptance rule.
3. Rewriting the sequence maximum height from 1088 to 1080 corrupts decoding.
   Geometry participates in prediction/filtering; identical superblock counts
   do not make that edit valid.
4. A valid render-size hint was inserted into the bounded synthetic frame-header
   OBUs, leaving coded dimensions and tile data intact. Offline decode succeeds,
   reports coded 1920×1088 and a 16:9 display aspect ratio, and retains padding.
5. The same header rewrite was exercised inside the native sender's callback.
   Electron's actual receiver decoded 329 distinct frames at **1920×1088**.
   It did not crop to the render height. The new framing gate rejects this run,
   despite successful transport, decoding and timing markers.

Evidence: `av1-render-hint.ivf`, `av1-render-bitstream.py`,
`av1-render-native-experiment.py`, `av1-render-hint.c`, and
`av1-render-native/report.json` under `out/encoder-parity/`. These are private
fixed-layout experiments, not a general-purpose AV1 header parser.

AV1 render dimensions are a display hint, not guaranteed decoded cropping.
WebRTC's dav1d wrapper has an optional crop field trial, disabled by default in
the reviewed upstream change. A sender cannot enable that trial in an
unmodified viewer. Enabling it in DPE alone would not fix stock Windows/browser
viewers. Receiver cropping, GPU scaling to a smaller aligned picture, and
software encoding are different tradeoffs; none is represented here as the
requested exact-framing, minimal-pass universal workaround.
[AV1 specification][av1-spec], [WebRTC crop default][crop-default]

## Earlier HEVC prototype and integration requirements

The private bridge uses a minimal FFmpeg 9.0.2 HEVC VA-API encoder and the
reviewed Discord C ABI. It sends real H.265 through Discord's RTP sender and
decodes through Electron's hardware receiver. The typed fixture now supports
`--encoder h265-vaapi` and uses real HEVC negotiation parameters; a H.264
payload or a receiver that never decodes does not count as success.

The best retained ten-second run, `hevc-native-rc-qp/report.json`, passed the
native fixture and retained exact 1920×1080 for 580 distinct decoded frames.
It required both real rate updates and the untrusted-QP correction. Without
those, early prototypes silently downscaled despite sufficient allocation.
The ordinary delivery pass alone did not identify that quality regression.

Source and build evidence:

- `out/encoder-parity/hevc-isolated.c`, `hevc-isolated-shim.c` and
  `hevc-rc-qp-experiment.py` implement the private bridge and hook injection.
- Official FFmpeg 9.0.2 archive SHA-256:
  `8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e`.
  Source, configuration log and reusable objects are retained in
  `out/encoder-parity/ffmpeg-build/`. The minimal build enables only VA-API,
  libdrm, HEVC encoding and required codec utilities, with hidden symbols.
- Direct system FFmpeg linking and a separate `dlmopen` namespace crashed in
  private tests. The working backend uses private static FFmpeg archives inside
  a local shared object, with hidden archive symbols and ordinary `dlopen`.
  The failed loader experiments and debugger evidence are retained.
- FFmpeg's initial rate-control globals normally apply at IDR. The private
  build submits the globals each frame, and the bridge updates RC/HRD/FPS
  parameters rather than merely changing `AVCodecContext` fields.
- Native clone-I420 returns a separate lease: release it with
  `cc_i420_buffer_destroy`, not `cc_video_frame_destroy`. The wrong destructor
  crashed an early private experiment. Original GOT page protection must also
  be restored, including writable lazy-linkage pages.

That earlier CPU-readback prototype was not suitable for production. The current
[implementation](hevc-av1-chroma-20261104.md) replaces it with GPU import,
truthful device probes, CPU-input support, dynamic rate updates, lifecycle checks,
source/license packaging and regression tests. Native sender/receiver HEVC now
passes, including framing checks. A rapid resize test exposed a driver failure;
fully retiring the old VA display before opening the new one resolved the
retained reproducer. Windows and live-call parity still need separate evidence.

## Acceptance scorecard

Use five independent gates. Score a gate only after its complete evidence is
present; unknown is not a pass. Overall acceptance requires **5/5**, and an
aggregate image-quality average cannot compensate for a failed framing gate.
Current end-to-end Windows parity is **unscored**. The local product now passes
HEVC and full-picture AV1 delivery checks; AV1 uses a slightly smaller resampled
raster, and the Windows/SFU comparison remains unmeasured.

| Gate                              | Evidence required to pass                                                                                                                                                                             |
| --------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Framing and codec correctness     | Exact full source picture with no padding on unmodified Windows/browser/native receivers; real H.264, AV1 and HEVC payloads; resize and codec changes                                                 |
| Compression at the same budget    | Matched Windows/Linux source frames and effective allocation; measured RTP payload budget; motion and text/edge quality, per-frame distributions and scene-cut recovery; no win credited to overshoot |
| Cadence, latency and resource use | Distinct decoded frame intervals and source age at 60/120 FPS, GPU/game load, CPU cost and copy/pass count; repeated runs; no worse than the measured Windows baseline                                |
| Network adaptation                | Actual SFU signaling/allocation plus controlled low-bandwidth/loss/recovery; bounded queues; preservation and restoration of negotiated limits                                                        |
| Reliability and delivery          | Truthful per-device probes, old/unsupported GPU fallback, session lifecycle, FPS/bitrate changes, tested package, required lint/tests and affected integration checks                                 |

The private fixture now rejects decoded frames larger than its explicit encoder
geometry. `--expect-resolution` additionally requires one exact rendition;
ordinary adaptation tests may still produce smaller frames. This prevents an
AV1 padding experiment from receiving a generic successful result just because
the timing marker survived.

Final validation: `bun run lint` passed; `bun run test` passed 686 Python tests
and 408 JavaScript tests, with two JavaScript checks skipped. A new ordinary
H.264 run used a 120 FPS synthetic GPU source, requested 1080p60/9 Mbps, reapplied
transport settings at three seconds and required exact 1920×1080. All twelve
fixture validation gates passed, with 455 distinct decoded frames in eight
seconds. The exact-source analyzer matched 240 recorded frames. Evidence:
`out/encoder-parity/{lint-final.log,tests-final.log,h264-final/report.json}`.
The private HEVC and failing AV1 render-hint receiver runs are separate evidence;
none is a Windows/live-call parity result.

The current local codec checks and shader correction are documented in the
[implementation report](hevc-av1-chroma-20261104.md). The remaining comparison
needs an actual Windows reference run and SFU allocation evidence. Passing local
Linux fixtures cannot establish the requested experience by themselves.
Hibernation was requested only after confident completion; that condition has
not been met.

[capture]: https://support.discord.com/hc/en-us/articles/9410427556375--Windows-Capturing-Application-Window-for-Screen-Share-and-Go-Live
[dxgi]: https://learn.microsoft.com/en-us/windows/win32/direct3ddxgi/desktop-dup-api
[amf-h264]: https://github.com/GPUOpen-LibrariesAndSDKs/AMF/blob/master/amf/public/include/components/VideoEncoderVCE.h
[amd-blog]: https://discord.com/blog/from-blocky-to-brilliant-improving-video-quality-on-discord-go-live-on-amd-gpus
[mesa-caps]: https://github.com/chaotic-cx/mesa-mirror/blob/mesa-26.2.5/src/amd/common/ac_video.c
[av1-issue]: https://github.com/GPUOpen-LibrariesAndSDKs/AMF/issues/423
[amf-av1]: https://github.com/GPUOpen-LibrariesAndSDKs/AMF/wiki/AV1-Encoder
[av1-spec]: https://aomediacodec.github.io/av1-spec/
[crop-default]: https://webrtc.googlesource.com/src/+/ecde63eab5b643e9d84220b8a50208b4cd8ad866
