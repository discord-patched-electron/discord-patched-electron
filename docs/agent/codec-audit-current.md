# Codec reliability follow-up

This retained audit predates the [GPU HEVC and AV1 alignment adapter](hevc-av1-chroma-20261104.md).
Its encode/decode matrix describes the earlier runtime test, not the new helper.

The native capability report on the retained runtime and current AMD driver is:
AV1 and H.264 encode/decode, VP8 encode/decode, H.265 decode only. VP9 is absent
from this native capability list. These are Discord backend capabilities, not
the GPU’s complete hardware capabilities. The GPU’s HEVC encoder works in an
independent VA-API test; see the [native HEVC gap](hevc-native-gap-20261103.md). The settings exclusion list is a restriction,
not a promise that every listed codec is available. No unavailable encoder or
hardware decoder was forced into negotiation.

Fixed a discovery race in `integration/video/codecs.cjs`: after the two-second
survey deadline, a late completion could overwrite the state of a replacement
probe and mark an obsolete attempt successful. Completion is now idempotent.
The new regression holds both promises and delivers them out of order.

Fifteen codec tests passed, including the retained Discord capability assignment
and fallback body with `DPE_CODEC_RENDERER_SOURCE`, all five exclusion names,
encode-only restriction, unchanged decode capabilities, camera/viewer isolation,
and native failure precedence. Scoped JS lint and formatting passed.

Fresh bounded native sender/receiver fixtures are under
`/mnt/storage/dpe-codec-audit-current/`. They use private synthetic PipeWire content,
fresh profiles and fixed fixture transport keys, without account access or load
benchmarks. H.264 VA-API to the native ffmpeg/OpenH264 receiver delivered 114 frames;
VP8/libvpx delivered 119. At 640×360, AMD VCN4 AV1 is deliberately rejected by
the existing coded-size guard. The explicit H.264 renegotiation fixture then
delivered 113 frames and passed its fallback gate. This fallback is synthetic
negotiation, not a live SFU test.

Aligned 1280×1024 AV1 VA-API encoding also passed native delivery, decoding with
dav1d (116 frames). This verifies an eligible aligned encoding size, not
arbitrary-resolution AV1 or hardware AV1 decoding. The attempted output-only
alignment control still produced 640×360 under the production profile and was
correctly rejected; the actual source-size check is the successful control.

No NVIDIA/Intel hardware, live viewers, or physical camera was tested. Receiving
hardware acceleration is not inferred from encode capability. Earlier native
AV1 hardware-decoder failures and dav1d recovery remain documented in
[screenshare codecs and viewing](screenshare-codecs-viewing-20261102.md).
