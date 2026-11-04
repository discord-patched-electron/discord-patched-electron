# AMD H.264 adaptation correction, 2026-11-03

Follow-up: the user found a [4 Mbps cap after reconfiguration](bitrate-cap-regression-20261103.md).
The synthetic checks below did not cover that later reconfiguration and must
not be treated as proof that the staged version preserved the full bitrate.

This continues the [quality-scaler investigation](quality-scaler-20261103.md).
The earlier preset-only candidate still shrank complex scenes. The final helper
corrects the quantizer contract and bases the replacement bandwidth decision on
the native encoder allocation. It preserves exact requested framing and leaves
the AMD AV1 1080p guard in place. No 1088-line padding workaround is enabled.

## Cause and implementation

AMD VBAQ can vary quantization within a picture. Discord's exposed H.264 header
QP is not a trustworthy average picture QP, but its native encoder leaves
`EncoderInfo::is_qp_trusted` unset, which WebRTC treats as trusted. The native
quality scaler therefore shrinks the image in response to this misleading
signal. Merely enabling balanced encoding and VBAQ did not prevent that.

The helper tracks only AMD H.264 encoder handles whose tuning it actually
changed. For those handles it marks the otherwise unspecified QP contract
untrusted, preserving explicit native declarations. It enables the existing
bandwidth scaler for this contract when Discord provides explicit quality
thresholds, without modifying the underlying stream configuration.

Two additional corrections are necessary:

- Reconfiguration temporarily reports an unknown encoder. Removing the active
  bandwidth resource during that interval resets its own resolution restriction.
  Retain that resource through the same manager's transient H.264 metadata gap.
  Known fallback, codec changes, absent settings and disabled/Source policy keep
  their original behavior.
- Encoded byte rate is not available bandwidth. A static screen can encode at
  0.5 Mbps despite a 9 Mbps allocation. Using that output rate caused an observed
  static-screen regression. Only the owned bandwidth decision's rate query now
  reads the actual native target allocation. Encoded bytes, media diagnostics,
  packet delivery and transport allocation are not rewritten. Native resolution
  limits, tolerances and up/down decisions remain in force.

Ownership is keyed by manager and scaler, removed before known backend fallback
and in both native scaler destructor paths. Zero/suspended target updates yield
an absent decision sample instead of reusing the manager's last nonzero budget.
Installation verifies the reviewed complete module hash, callsites and ABI.
Rollback retains a trampoline if restoring a patched callsite fails. Allocation
and transition support install before QP correction and AMD tuning.

This is an AMD VBAQ correction, not a global QP-scaling disable. Other encoders
retain their native contracts. Intel/NVIDIA compatibility was reviewed in source;
those hardware paths were not exercised here. The settings label now says
**Reported quantizer (codec-specific)** rather than implying a frame average.

Primary contract references:

- [WebRTC EncoderInfo](https://github.com/webrtc-mirror/webrtc/blob/main/api/video_codecs/video_encoder.h)
- [WebRTC resource manager](https://github.com/webrtc-mirror/webrtc/blob/main/video/adaptation/video_stream_encoder_resource_manager.cc)
- [WebRTC bandwidth scaler](https://github.com/webrtc-mirror/webrtc/blob/main/modules/video_coding/utility/bandwidth_quality_scaler.cc)
- [Chromium VAAPI encoder contracts](https://github.com/chromium/chromium/blob/main/media/gpu/vaapi/vaapi_video_encode_accelerator.cc)

## Bounded native validation

Evidence: `out/quality-regression/qp-contract-verdict.json`. Synthetic content,
private authenticated relay and real native encoding/decoding were used. No
account calls or user content were captured.

- Final static scene: 18 seconds, 1,058 distinct decoded frames, exact 1920×1080
  throughout and no quality adaptations despite low actual output bitrate.
- Final complex scene with an unchanged native 9 Mbps allocation: 1,640 distinct
  decoded frames, exact 1920×1080 throughout. Changing the synthetic SFU cap alone
  did not change the allocation; that run is not a down/up adaptation test.
- Final native transport rate-step: 27 seconds, configured caps 9→1→9 Mbps.
  Actual allocation reached about 974 kbps and recovered. Receiver dimensions
  followed 1920×1080 → 1486×836 → 1148×646 → 1480×832 → 1920×1080. The copied
  fixture uses its existing rate control with divisor 9 instead of 4. No packet
  loss was injected. Generic delivery/codec/adapter checks passed.
- The rate-step run has a **2.28-second receiver gap during recovery**, without
  decoder errors. Do not claim seamless recovery from this result.
- An earlier 240 FPS producer run retained 1080p with 645 distinct decoded
  frames, but predates the final allocation adapter. The final adapter has not
  been validated against Minecraft at 2,000–4,000 FPS or a live Discord SFU.

Rejected intermediate implementations remain in the evidence. In particular,
`qp-static-retained` demonstrates the encoded-rate regression and was not staged.
The final helper is
`out/quality-regression/qp-allocation-rate-step/helper-build/libdpe-camera-probe.so`,
SHA256 `949f60e2f4f582a3d9fcc2052b37b7fc70adc92d924631ffea3d81f06489edd2`.

Full checks: 675 Python tests passed; 400 JavaScript tests passed and one skipped.
The real fresh-profile Screensharing settings harness passed at
`/mnt/storage/dpe-quality-20261103/settings-qp-contract/settings.json`.
Independent final source/disassembly review found no actionable blockers.
Final lint and candidate manifest verification are recorded by staging.

The correction addresses the observed unnecessary resolution reductions; it
does not establish that every low-quality report is fixed. Discord's existing
bitrate ceiling remains, so complex scenes can still exceed the image detail
that H.264 can preserve at that budget. Genuine congestion still adapts down.
