# Native AMD AV1 two-row padding, 5 November 2026

Follow-up: [native software AV1 cropping](software-av1-crop-20261105.md) extends
automatic trimming to DPE's dav1d receiver. The decoder limits below describe
the earlier implementation.

This extends [automatic AV1 cropping](automatic-av1-crop-20261105.md). Earlier
work rejected 1082 as insufficient to provide an exact 1080-line picture to
ordinary receivers. With an explicit source rectangle and a matching DPE
receiver, that limitation no longer prevents using the cheaper encode path.

## Driver behaviour and scope

[AMD's AMF documentation](https://github.com/GPUOpen-LibrariesAndSDKs/AMF/wiki/AV1-Encoder#av1-specific-api)
describes 1080-line input producing 1082-line output, with two black bottom rows.
The same hardware trick is available through Mesa VAAPI; switching to AMF or
installing another driver is unnecessary for this implementation.

Mesa's
[VCN AV1 session setup](https://chromium.googlesource.com/external/gitlab.freedesktop.org/mesa/mesa/+/c9a8dcd63ce2356d5c575f3c4149ba68bd5c8624/src/gallium/drivers/radeonsi/radeon_vcn_enc.c)
uses `height + 2` when the height is divisible by eight but not sixteen on
pre-VCN5 hardware. The optimized path requires a width divisible by 64, so
firmware only needs to add the two bottom rows. It applies generically:
640×360 becomes 640×362, 1280×712 becomes 1280×714, and 1920×1080 becomes
1920×1082. Other unaligned geometry retains the existing centred-padding path.

The backend enables this only for an AMD Mesa driver and kernel-reported VCN
major 4 on the **actual selected render node**. It resolves the node's device
number through `/sys/dev/char/…/device/ip_discovery/…/UVD/…/major`; all reported
instances must agree. Unknown discovery data keeps the previous path. Intel,
NVIDIA, non-Mesa drivers and other AMD generations do not acquire this quirk.

## Implementation

`integration/video/hevc-backend.c` keeps the source-sized input surface and
FFmpeg input context. Mesa/firmware writes the additional rows. Callback
metadata now reports the actual encoded dimensions separately from input
dimensions; crop insertion compares against those encoded dimensions. The
existing crop metadata describes origin (0, 0) and the full original source.

This bypasses the helper's VPP context, padded output pool and once-per-surface
black initialization for the eligible geometry. The existing DMA-BUF import
and producer lease stay intact. Other shapes still use the tested centred VPP
path. Resize recreates the relevant codec/pools and reannounces the source
rectangle on keyframes. A genuine aligned frame has no crop metadata.

No Electron rebuild or additional decoder change was needed. Cropping retains
the [existing decoder scope and proposed-metadata limitations](automatic-av1-crop-20261105.md).
Unmodified receivers and Discord's separate native dav1d decoder still display
the two black rows; the change does not make those receivers crop automatically.

## Validation

Artifacts: `out/av1-1082-20261105/`. Only generated pixels and private native
loopback connections were used. No account call or Discord server forwarding
was tested; locally unavailable GPU vendors were reviewed, not runtime-tested.

The initial direct-encode probe decoded all three 1920×1082 frames. Original
1080-line source detail remained in its original coordinates, with mean luma
error below 0.07 and maximum error six in the first frame. There were no pixels
over the 20-level detail-error threshold. The two bottom rows had neutral
chroma; firmware luma was 3, which decodes to black in limited range.

Final generated pixel checks cover twelve HEVC/AV1 cases, including native
640×362, 1280×714 and 1920×1082, aligned frames, and the centred 1918×1078 path.
The padding gate accepts luma at or below black level 16 while independently
requiring neutral chroma; it does not require firmware's black to equal 16.

The lifecycle fixture now emits 21 frames per codec, exercises suspend/resume,
rejected-update recovery, 1080 → irregular 1078 → 1080 → genuine 1088 changes,
and checks crop replacement and clearing plus fresh-decoder keyframe recovery.

VAAPI traces of the same three generated 1080p frames show three
`VAProcPipelineParameterBufferType` submissions on the previous centred-1088
backend and **zero** on the native-1082 backend. Both still encode three frames.
This verifies removal of the padding pass, not a measured live-call latency or
FPS improvement. Raw traces and extracted driver source remain in the artifact
directory.

Native Discord loopback results:

| Path                      | Source    | Displayed | Checked edge frames |
| ------------------------- | --------- | --------- | ------------------- |
| Updated Electron, 60 FPS  | 1920×1080 | 1920×1080 | 354                 |
| Previous Electron, 60 FPS | 1920×1080 | 1920×1082 | 355                 |
| Updated Electron, 30 FPS  | 640×360   | 640×360   | 178                 |
| Native dav1d, 60 FPS      | 1920×1080 | 1920×1082 | 349                 |

All passed. The uncropped paths additionally checked the black padding on every
listed frame. Their first fixture runs rejected the 1082 geometry in the Python
validation gate despite successful decoding and pixel/edge checks. The gate
now allows the two-row shape only for matching VCN4/source geometry with
independent passing padding evidence. A regression test covers accepted and
rejected shapes; fresh full native runs then passed. Original failed reports
are retained alongside `old-receiver-gated/` and `software-receiver-gated/`.

Repository checks passed: `bun run lint`, 707 Python tests and 426 JavaScript
tests, with three environment-dependent JavaScript skips. The current helper
was staged for the existing experimental sender/viewer launchers; the runtime
and native module were preserved. See `staged.json` for hashes. Running
instances were left alone and need a restart to load the new helper.
