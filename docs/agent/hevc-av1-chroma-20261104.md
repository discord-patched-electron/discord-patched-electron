# Hardware codecs and GPU chroma filtering, 4 November 2026

The working tree now implements real HEVC VA-API encoding, a GPU AV1 alignment
workaround, and filtered GPU chroma conversion. Electron was **not rebuilt**.
These pass isolated native sender/receiver tests on the RX 7900 GRE. They do
**not** establish measured Windows or live Discord SFU parity.

## Production implementation

`integration/video/hevc-backend.c` supplies the versioned C API in
`encoder-abi.h`. A private minimal FFmpeg 9.0.2 build enables only the required
VA-API HEVC/AV1 encoders and codec utilities. Its symbols are hidden inside an
embedded DSO to avoid binding to Electron's FFmpeg. Direct system linking and
`dlmopen` were rejected after earlier crashes. The official archive is pinned
in `scripts/build_hevc_support.py`; retained local objects are reused.

`encoder-hevc.c` binds the reviewed native frame entrypoints and surface/Arc
accessors. GPU input imports the existing NV12 DMA-BUF and retains its Rust
producer lease through GPU consumption. The ordinary GPU HEVC path adds no
CPU readback, conversion or upload. CPU inputs use their proper by-value I420
ABI and upload independently. Native NVENC and other codecs retain their
original frame functions.

HEVC capability comes from encoding a small picture and observing real
VPS/SPS/PPS/IDR data on that render device. It is not an unconditional capability
override. Native NVIDIA capability stays authoritative. Missing libva/backend
support preserves native codec behavior and the H.264 corrections. An optional
`DPE_HEVC_BACKEND` path permits replacing the private library. Application
payloads ship the LGPL license, adapter/build source and complete pinned FFmpeg
archive. Package dependencies include libva; glibc inspection includes the
embedded DSO's version requirements.

Bitrate/FPS changes update VA rate, HRD and frame-rate state. The private FFmpeg
patch submits those dynamic parameters when changed between IDRs, while static
quality parameters retain their normal IDR behavior. Reapplying identical
settings does not reset the encoder. Quantizers from these replacement backends
are explicitly untrusted rather than masquerading as frame-average QP.

HEVC level selection includes sample rate as well as picture size and peak
bitrate, using session ceilings and the HEVC Main-tier limits. FFmpeg's default
automatic selection omits frame rate. The new helper rejects unsupported levels
and recreates the encoder when an update actually requires a higher level;
ordinary rate/FPS updates within its declared envelope keep the hardware session.
This changes the signaled level, not the requested bitrate. Native wire checks
at 60 and 120 FPS are retained in `hevc-level-{60,120}/report.json`.

## AV1 without visible padding

VCN4 cannot reliably encode arbitrary unaligned AV1 geometry. Padded frames
plus render-size hints were rejected: the tested ordinary receiver kept the
extra rows. Changing the sequence dimensions corrupts entropy decoding.
AMD documents the same hardware limitation on Windows in
[AMF issue 423](https://github.com/GPUOpen-LibrariesAndSDKs/AMF/issues/423).

The new path resamples the **whole** NV12 source to the nearest smaller aligned
raster, using one VA video-processing pass before hardware AV1 encoding. No
padding is created, no source edges are cropped, and GPU input stays on the GPU.
1920×1080 becomes **1920×1072**, not native 1920×1080. This introduces slight
vertical resampling and changes the raster aspect ratio by about 0.75%; it must
not be described as pixel-exact 1080p or a universal receiver-side crop.
The actual encoded dimensions are reported honestly. Already aligned pictures
keep the original native AV1 encoder. The workaround requires verified VCN4
scope and a working device; AV1 is not disabled merely because HEVC is
unavailable on a distribution.

The framing fixture places colored side/bottom edges and a white marker in the
last four source rows. Removing eight source rows or appending black rows fails
the check. The actual Electron receiver passed every checked frame at 60 FPS
and 120 FPS. This establishes full-picture preservation on that receiver, not
measurements on a Windows machine or every stock Discord receiver.

## Capture quality defect

The native fused RGB-to-NV12/I420 shader chose chroma from the top-left pixel of
each 2×2 group. Motion at odd pixel positions produced substantially different
sampled RGB error from even positions, independently of the codec. Earlier
comparisons containing only even-position frames overstated quality.

`integration/video/shaders/chroma-420.comp` averages the four samples before
writing subsampled chroma. Scaling, luma conversion and both output layouts stay
in the same dispatch. This costs three extra texture samples for one quarter of
the pixels, without another GPU pass or CPU conversion. Box filtering is also
used by [libyuv's conversion implementation](https://chromium.googlesource.com/libyuv/libyuv/+/refs/heads/main/source/row_common.cc).

The project-owned SPIR-V preserves the reviewed embedded slice length and
interface. `patch_capture_chroma.py` verifies original/replacement shader hashes
and complete input/output module hashes, then atomically replaces only the
prepared file. Old source inodes remain untouched. Preparation order is Pulse,
HRD, visible dimensions, buffered decoder output, then chroma. Re-preparation
and all native scopes recognize the exact final hashes:

- Earlier module: `85baa5b9c9c597abcce5182249dd7b551c60157d3061c321ad6aed3d433ed008`.
- Pinned module: `8510b483b6618f171da32013cb768be45470bdc517534a1ed861a8d946a120fa`.

Rebuild the shader with `scripts/build_capture_shader.py` and review new output
identities before changing the shipped asset. The build uses glslang and
SPIR-V tools, independently of Electron. Runtime/package installation does not
compile it.

On 143 common synthetic source sequences after startup, the corrected H.264
run had lower sampled RGB error and fewer payload bytes than its control.
This is useful local evidence, not VMAF, a matched effective-allocation study,
or a Windows quality score. Preserve odd/even motion coverage in future tests.
Evidence: `out/hevc-implementation/{original-h264-vaapi,filtered-h264-vaapi-v2}`.

## Lifecycle failure and fix

Rapid HEVC 256×256 → suspend → 640×360 resume exposed VCN ring timeouts in the
initial implementation. The kernel identified the isolated `lifecycle` test
process. Disabling VBAQ, changing SPS geometry, sharing/replacing codec contexts
and opening a replacement display before destroying the old one did not fix it.
Those geometry and quality experiments were rejected.

The working sequence closes the old codec **and VA display completely before
opening the replacement display and codec**. It keeps the application's public
encoder handle stable. Invalid options are rejected before teardown; failed
initialization attempts recover previous usable settings when possible.
Suspension tracks the last opened geometry separately from requested geometry,
so resuming at a different size cannot retain the old hardware context.

The complete generated-pixel lifecycle passed five consecutive runs after the
fix, for both codecs. Independent FFmpeg decoding also confirmed all nine
pictures in each codec's resize/suspend/recovery run. No further VCN timeout was
observed in the subsequent native tests. Keep the failure evidence; an ordinary
steady-state delivery pass would not have caught this.

```sh
uv run python scripts/check_vaapi_backend.py \
  --backend out/av1-alignment/level-fixed/libdpe-hevc.so \
  --render-node /dev/dri/renderD128 --output out/new-lifecycle-check
```

## Validation and remaining parity gates

Final native evidence is under `out/av1-alignment/`:

- `verified-lifecycle/report.json`: both codecs, nine independently decoded
  pictures each, including resize and suspension.
- `lifecycle-repeated.json`: five consecutive successful hardware lifecycle runs.
- `final-hevc-framing/report.json`: exact 1920×1080, all 460 edge checks pass;
  477 GPU frames, no CPU frames, import fallbacks or failed submissions.
- `final-av1-framing/report.json`: 1920×1072, all 474 edge checks pass;
  478 GPU frames, no CPU frames, import fallbacks or failed submissions.
- `final-av1-rate/report.json`: 18-second bitrate reduction/recovery passes,
  1,078 GPU frames, no failed submissions.
- `final-av1-120/report.json`: 240 FPS generated GPU source, requested 120 FPS;
  all 936 edge checks pass, 956 GPU submissions, no CPU path or failed frames.
- `native-cpu/report.json`: CPU-input AV1 resampling and native delivery pass.

The [Windows audit](windows-encoder-parity-20261104.md) remains the comparison
reference. Its five acceptance gates still need a matched Windows runtime,
real SFU allocation and game-load comparison. Intel, multi-GPU device selection,
other driver versions and stock Windows viewers remain unmeasured. HEVC still
requires a compatible negotiated receiver; no software HEVC decoder was added.
The user requested hibernation only after confident completion of parity;
that condition remains unfulfilled.

Final required checks passed: `bun run lint`; `bun run test` with 696 Python
tests and 409 passing JavaScript tests (two skipped). Logs are
`out/av1-alignment/delivery-{lint,tests}.log`. The final rebuilt backend also
passed independent lifecycle decoding in `delivery-lifecycle/report.json`.
`keyframe-recovery/report.json` and `level-recovery/report.json` additionally
require a new decoder to start successfully from a requested recovery keyframe,
for both HEVC and AV1.

The H.264 high-capture-FPS audit found **60/1** in every VA rate-control update
with a 120 FPS source and requested 60 FPS output. Its lower measured byte count
therefore does not justify changing the driver's FPS or raising a bitrate cap.
Evidence: `h264-fps-audit.log` and `h264-fps-audit/va-trace*`.
