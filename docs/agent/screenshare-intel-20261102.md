# Intel screenshare compatibility: source audit, 2 November 2026

No Intel hardware was available. This is a source and reviewed-native-code audit,
not an Intel performance, import, encoder or receiver test. No hardware launch,
build or production edit was performed for this note. The audit found no
confirmed Intel-specific regression requiring a new vendor workaround.

## Pinned inputs

Downloaded files and tree manifests are retained in
`out/intel-research-20261102/`, with full revisions in `pins.json`.

| Project                               | Reviewed revision                                                                                               |
| ------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| [Intel media-driver][driver]          | `e72b3f9dcd032b2bf0dfc5fc2fbe4481d11fd3cb`                                                                      |
| [oneVPL API][vpl-api]                 | `674d015bcb294bc39fa276e99a652ea045423e82`                                                                      |
| [Intel oneVPL GPU runtime][vpl-reset] | `c2052626b7f62f30530cf1fc8b73b74e586df707`                                                                      |
| [libva][va]                           | `f052e984e0c8bf10784198cc311b23c567b1367f`                                                                      |
| [OBS VA-API/QSV][obs-va]              | `65c320b46e1af5c7a555161aea6182cdf7a8c524`                                                                      |
| [GPU Screen Recorder][gsr]            | `099a55b`, snapshot `r1536`; archive SHA-256 `699ee6af0530e01d446c25e1e1372e21d97502f5484c9bd6a09a665c91024483` |

GSR files come from the same-day retained archive and
`out/capture-research-20261102/source-manifest.json`; its current web tree was
access-controlled. Native addresses below refer to the reviewed module with
SHA-256 `72096efc87a407b975e9913477d387e2de9a1f1028575c1f97a22365bacb007c`.

## What the current Linux path actually uses

The native `cc_encoder_create` switch at `0xe2a5e5` sends backend type 6 to
`VulkanBackend` VA-API construction at `0xe569f0`. Legacy enum values
`Intel=3` and `IntelDx11=4` reach the unsupported path at `0xe2a6d1` in this
Linux binary. Intel on Linux therefore does not gain QSV/oneVPL by changing an
encoder name or choosing the legacy enum. The separate NVIDIA audit retains the
dispatch extraction in `out/nvidia-research-20261102/native-create-dispatch.json`.

`RustEncoder::GetEncoderInfo` at `0x4bafc4–0x4bafe7` identifies vendor 2 as
`intel: vaapi`. The existing diagnostics' `vaapi` match correctly classifies
that implementation. The legacy strings `intel: swsurf` and `intel: direct3d`
exist, but their constructors are unsupported here; broadening the Linux
hardware-name heuristic is unnecessary.

Both `INTEL_GPU_DISABLE` setters in retained renderer
`out/webcam-client-review/web.8fb853a63180b897.js` are explicitly Windows-only,
with additional Intel/integrated-device/experiment conditions. The transport
consumer translates the flag into `intel-gpu=0`. Bundle SHA-256 is
`6c6ce4690146209c83f1683684710d6c921875fe54746e4cc37b80816840a218`.
There is no demonstrated Linux Intel rollout exclusion to remove. The DPE codec
adapter leaves native capability intersection, codec priority, explicit
denylists and encoder-failure recovery intact.

## Compatibility findings

**Visible dimensions and adaptation.** Intel's [AV1 basic feature][driver-av1]
keeps the requested picture width/height separately from macroblock and
superblock storage alignment. [OBS QSV][obs-qsv] likewise uses visible
`CropW/CropH` with aligned `FrameInfo.Width/Height`. GSR's VA-API implementation
uses even visible dimensions on Intel; its larger visible-padding workaround is
AMD-specific. These support keeping the capture correction's even visible
dimensions and the scaling hook's bound-safe even floor. They do not prove that
every imported DMA-BUF modifier or allocation layout works on Intel.

`encoder.c` checks the actual selected vendor before its VCN4 size rejection.
Intel vendor 2 cannot trigger the AMD guard, even when an AMD device is also
present. Intel's normal AV1 levels and lifetime checks still apply through the
shared VA-API backend. Do not impose AMD's 64×16 visible rule on Intel.

**Capabilities are device-specific.** Intel's [feature tables][features] list
AV1 encoding for DG2/ATSM and MTL-family/newer platforms; DG1/TGL-family AV1
decoding does not establish encoding support. The driver documents HuC
dependencies for low-power bitrate control and entrypoint consolidation from
MTL. [OBS queries][obs-probe] the actual profile, entrypoint and rate-control
attributes rather than treating the Intel vendor as sufficient. Native
`find_supported_encode_profile` at `0xe25cb0` checks AV1 profile 32 and encode
entrypoints 6/8 (`EncSlice`/`EncSliceLP`), retaining a real capability filter.

The reviewed [Xe_LPM+ AV1 capability table][intel-caps] advertises a minimum
surface of 128×96, NV12/P010, and particular rate-control modes. Tiny windows or
very small adaptations must retain initialization/fallback handling; do not
assume arbitrary even rectangles work or pad the visible stream to manufacture
support. A future native capability API should expose the selected device's
minimum/maximum surfaces and rate-control modes. No universal Intel minimum is
inferred from one platform table.

**AV1 signaling and changes.** The driver [DDI parser][intel-ddi] copies the
application's level and visible frame dimensions. The existing shared correction
replaces the native invalid level-zero default with a calculated level and
preserves Main tier. Normal bounded cases already covered by the offline table
include 720p30 at level index 5, 1080p60 at 9 and 4K60 at 13, at their tested
bitrate bounds; those are not Intel hardware results. Intel's [oneVPL runtime][vpl-reset]
also calculates levels and treats a changed sequence level as requiring a new
sequence/keyframe. Rejecting an update beyond the emitted level through native
fallback is conservative; silently retaining an inadequate level is not.

There is an API-comment discrepancy: [libva's AV1 header][va-av1] documents
`seq_level_idx` as 0–23, but Intel's actual
[`codec_def_encode_av1.h`][intel-level] explicitly permits 0–23 and 31. The DDI
copies it without a level clamp; the 51 reviewed common AV1 implementation/header
files contain no level-index table access or rejection, only diagnostic output.
There is no source evidence here for rejecting the helper's unconstrained level
31 on Intel. Keep current behavior; do not add fallback based only on the
narrower API comment. This is source evidence, not a hardware conformance claim.

**HRD, rate control and resource lifetime.** [libva][va] defines buffer size and
initial fullness separately; the reviewed H.264 argument-order correction fixes
those values in both VA-API surface backends. It changes neither Intel capability
selection nor allocation/synchronization. No Intel exception is justified.
oneVPL/OBS parameter-query and reset handling are useful design references for a
future QSV backend, but are not a drop-in replacement for this Rust VA-API path.

VA surface export itself does not synchronize external GPU accesses. libva
requires completed external writes before VA reuse; GSR explicitly synchronizes
its exported surface, and oneVPL requires asynchronous completion and surface
ownership handling. The dimension, scaling and HRD fixes preserve existing
fences, buffer ownership and pool lifetime. Removing waits or recycling a busy
surface to reduce latency would need independent synchronization proof.

## Recommended action and limits

Keep the existing vendor-neutral dimension/HRD/scale corrections and the narrowly
scoped AMD guard. Preserve source-verified regression cases for Intel VA-API
1080p and adapted even dimensions with AMD present, plus legacy Intel enum
pass-through; root added these to the offline guard fixture during this audit.
Do not force an Intel codec, remove the Windows-only safety gate, add a QSV
backend, or modify firmware/kernel settings from this evidence alone.

Future Intel validation should check an actual AV1-capable device and an older
H.264-only device, visible geometry, changing FPS/bitrate, tiny sources,
DMA-BUF modifiers and the measured encoder/decoder implementation. Throughput,
quality, firmware readiness and cross-device import remain unverified. OBS or
GSR's success on a different allocation path is not proof for Discord's path.

[driver]: https://github.com/intel/media-driver/blob/e72b3f9dcd032b2bf0dfc5fc2fbe4481d11fd3cb/README.md
[features]: https://github.com/intel/media-driver/blob/e72b3f9dcd032b2bf0dfc5fc2fbe4481d11fd3cb/docs/media_features.md
[driver-av1]: https://github.com/intel/media-driver/blob/e72b3f9dcd032b2bf0dfc5fc2fbe4481d11fd3cb/media_softlet/agnostic/common/codec/hal/enc/av1/features/encode_av1_basic_feature.cpp
[intel-caps]: https://github.com/intel/media-driver/blob/e72b3f9dcd032b2bf0dfc5fc2fbe4481d11fd3cb/media_softlet/linux/xe_lpm_plus_r0/encode/av1/ddi/capstable_data_av1_encode_xe_lpm_plus_r0_specific.h
[intel-ddi]: https://github.com/intel/media-driver/blob/e72b3f9dcd032b2bf0dfc5fc2fbe4481d11fd3cb/media_softlet/linux/common/codec/ddi/enc/ddi_encode_av1_specific.cpp
[intel-level]: https://github.com/intel/media-driver/blob/e72b3f9dcd032b2bf0dfc5fc2fbe4481d11fd3cb/media_common/agnostic/common/codec/shared/codec_def_encode_av1.h#L229
[vpl-api]: https://github.com/oneapi-src/oneVPL/blob/674d015bcb294bc39fa276e99a652ea045423e82/doc/spec/source/programming_guide/VPL_prg_encoding.rst
[vpl-reset]: https://github.com/intel/vpl-gpu-rt/blob/c2052626b7f62f30530cf1fc8b73b74e586df707/_studio/mfx_lib/encode_hw/av1/agnostic/base/av1ehw_base_general.cpp
[va]: https://github.com/intel/libva/blob/f052e984e0c8bf10784198cc311b23c567b1367f/va/va.h
[va-av1]: https://github.com/intel/libva/blob/f052e984e0c8bf10784198cc311b23c567b1367f/va/va_enc_av1.h#L245
[obs-va]: https://github.com/obsproject/obs-studio/blob/65c320b46e1af5c7a555161aea6182cdf7a8c524/plugins/obs-ffmpeg/obs-ffmpeg-vaapi.c
[obs-probe]: https://github.com/obsproject/obs-studio/blob/65c320b46e1af5c7a555161aea6182cdf7a8c524/plugins/obs-ffmpeg/vaapi-utils.c
[obs-qsv]: https://github.com/obsproject/obs-studio/blob/65c320b46e1af5c7a555161aea6182cdf7a8c524/plugins/obs-qsv11/QSV_Encoder_Internal.cpp
[gsr]: https://git.dec05eba.com/gpu-screen-recorder/tree/src/encoder/video/vaapi.c?id=099a55b
