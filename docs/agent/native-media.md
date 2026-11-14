# Native media compatibility

Use [dpe-native-patches](../../agents/skills/dpe-native-patches/SKILL.md) before
changing proprietary instructions, ABI scopes or compiled hooks. Current locks,
patch tables and scope generators define supported identities; historical offsets
and hashes below explain decisions, not permission to accept a new binary.

## Native patch chain

Preparation applies the complete ordered Pulse/Krisp/HRD/dimension/decoder changes
before generating capture, camera, encoder and picker scopes. Each edit validates
whole-file input identity, exact original instruction bytes/targets, expected
output and executable bounds; atomic replacement and idempotent re-preparation
must preserve hardlinked source inputs. Recognizing a new hash alone is not a
port. Python scope tables and compiled selection/publication-wake tables must
agree with the actual final bytes. `supported: false` is not successful scope
qualification just because scope files exist.

The microphone guard changes whole-module identity before later HRD/dimension
patches. Stage all patch scripts together and regenerate every scope; never
replace an installed `discord_voice.node` alone. Pulse's
SetMicrophoneVolume hotplug path must reject channel counts outside 1–32 and
invalid volumes through the normal unlock/error epilogue while holding its
existing lock. Do not clamp values, invent channels, hide device events or change
unrelated buffer ABI. Executable fixtures cover lock balance, valid/invalid
values, repeated preparation and changed-byte rejection.

Retained decoder work repairs send/receive EAGAIN and buffered output without
losing packet/frame metadata, leaking buffers or assuming one output per input.
Use the executable fixture and actual native receiver, including send errors,
cleanup and idempotence; JS capability lists cannot prove it. Review immutable
candidate diffs through [the upstream workflow](upstream-update-review.md).

## Camera startup

Fast format probing intercepts VIDIOC_TRY_FMT only inside the reviewed
DeviceInfoV4l2::FillCapabilities range of the exact loaded module. Skip only
unadvertised formats; supported/unknown/failed enumeration and other callers
retain driver adjustment semantics. Do not touch S_FMT, cache stale device state,
start capture or keep it active. Unknown layouts need review.
`DPE_CAMERA_FAST_PROBE=0` disables the helper.

`asyncVideoInputDeviceInit` enables Linux's existing asynchronous native path so
capability probing does not block the voice command queue.
`DPE_CAMERA_ASYNC_INIT=0` restores the synchronous control. No Electron rebuild
is needed for either. The helper library path must work through packaged names
with spaces/semicolons without leaking Electron libraries into system children.
See [timings](benchmarks.md#camera-and-krisp) and
`check_camera_startup.py --compare-async`. Keep only timing/dimension metadata;
never serialize, display, save or upload physical-camera pixels or screenshot
the desktop during a physical test.

## Webcam flip

`openasar.desktop.webcamFlip` flips native camera Y/U/V planes after successful
ConvertToI420, before preview or encoder delivery. Respect odd dimensions,
strides and rotation; failed conversion passes through. It targets the reviewed
V4L2 camera call, not shared conversion, remote frames, screenshares or NDI.
The runtime hook validates module checksum, normalized function fingerprint,
executable range and original relative target; install once before engine init,
restore RX protection and update only an atomic flag on saves.

Remove Discord's self-preview CSS reflection from actual native video wrappers
(including pre-call camera wrappers) so sent and preview orientation agree.
Remote rendering remains unmirrored; there is no independent preview preference.
Delayed initial IPC cannot overwrite a later save. Native generated-pixel,
stride/rotation and scoped-preview fixtures passed; a real call is separate.
Evidence: `out/webcam-client-review/` and `out/webcam-flip-matched-settings/`.

## Codec availability and library paths

Capabilities belong to the actual native backend, selected GPU and receiver.
Settings exclusions restrict encoders; they cannot create a missing encoder or
remove decode/payload support. Keep native failure precedence and isolate camera,
viewer and unrelated connections. Late survey results cannot overwrite a newer
probe. Software decode must remain labelled software.

The reviewed Linux VP9 factories return no codecs/null and contain a fatal
encoder stub; advertising VP9 would not supply an implementation. PE parser/RTP
strings do not prove Windows encoder support. The native Linux VAAPI backend
originally had AV1/H.264 only despite working AMD HEVC hardware. The independent
HEVC backend supplies the missing C-ABI implementation; retain create/update/
destroy, native frame import, callbacks and capability gating, then qualify
actual sender/receiver delivery before exposure. Intel/NVIDIA/Windows source
review is not hardware validation.

H.264 decoding must resolve the unique actually preloaded managed Cisco OpenH264
library when an enabled decoder requests a missing/disposable Discord-cache path.
Do not copy the older library into Discord's independently managed cache, rewrite
explicit custom paths or force hardware preference. Reject ambiguous/deleted
mappings. The regression intentionally omits the environment hint and supplies
a stale incoming path before decoding distinct frames.

## Encoder ownership and bitrate

GPU frame imports require verified selected render node, format/modifier/planes,
fences and bounded lifetime. Source storage stays leased until actual GPU read
completion, not merely a returned PipeWire buffer. Import caches bind device,
inode/layout and ownership; resize, failures and teardown retire them. Unknown
formats retain safe fallback, without hidden full-frame CPU readback or fabricated
zero-copy claims. Native encoder adapters must preserve packet callbacks,
rate-control updates and congestion behavior.

Keep HRD bitrate reconfiguration and source-budget policy scoped to reviewed
methods. Discord scales viewer demand against the encode raster; the source
ceiling fix uses the smaller stable source/selected raster for the maximum only.
A full 1080p source selected at 1440p should retain a 9 Mbps maximum instead of
5,062,500 bps. Preserve native geometry, minimum/startup targets, explicit lower
caps, cameras/viewers and congestion allocation. Missing metadata uses original
policy. The optional smaller-viewer preservation heuristic remains independent.
The 8 November JS change had lint only at the user's request; live server
allocation/quality was not verified.

## AV1 padding and cropping

VCN4 can fail or pad eligible AV1 sizes. The specialized fast path requires AMD
Mesa and kernel-reported VCN major 4 for the actual node, consistent across all
discovery instances, width divisible by 64 and height divisible by 8 but not 16.
It keeps source-size input while firmware adds two rows: 1920×1080→1920×1082,
640×360→640×362. Unknown hardware uses the earlier centered-padding path.
Other geometry retains its existing alignment guard. Never guess a crop from
1920×1082/1088 alone: genuine images of those sizes must remain unchanged.

The sender records the source rectangle in bounded AV1 metadata; keyframes,
resolution changes and metadata-free resets replace/clear state. Matching DPE
receivers apply an explicitly validated visible rectangle while preserving
allocation/reference ownership, stride, timing and all picture edges. Unmodified
receivers can display padding. The metadata uses an open AOM proposal: FFmpeg's
raw-OBU demuxer rejected its trailing-bit form while dav1d/IVF and the native path
decoded unchanged packets. Do not strip bytes to make a tool pass or claim
universal forwarding/decoder support. Revisit syntax/type if the proposal changes.

The scoped dav1d hook does not link the host library. Reviewed ABI offsets are
input timestamp 24, output timestamp 72, picture dimensions 56. These are
snapshot-specific, validated against wrapper loads and complete function hashes.
Each decoder tracks private submission IDs and crop snapshots; delayed/reordered
output restores its original timestamp/crop. EAGAIN/rejected input does not
commit state. Context and snapshot registries cap at 128; missing/evicted entries
leave geometry unchanged. Closing/reusing a pointer removes state. Adjust only
validated 8-bit I420/I444 visible plane views, leaving allocator/reference geometry
and strides intact; no pixel copy or extra GPU pass.

Generated native loopbacks verified 353/357 1080p edge frames cropped exactly,
177 irregular 1918×1078 frames and 355 genuine 1920×1088 frames unchanged, with
zero rejected/unmatched crops or live contexts after teardown. Evidence:
`out/software-av1-crop-20261105/`, `out/av1-1082-20261105/` and
`out/automatic-av1-crop-20261105/`. Three-frame VAAPI traces show three padding
VPP submissions on the centered-1088 path and zero on native-1082; this proves
removal of that pass, not a live-call FPS/latency improvement. Old Electron and
unmodified software receivers retain their documented padding limits. Inspect
actual compiled runtime provenance before claiming a source port is active.

## Presentation timing

Receiver presentation-clock and source pacing changes must keep original RTP/NTP
metadata, own one frame lease at a time and handle source replacement/teardown.
Do not relabel poll/submission counts as unique displayed frames or turn off
rate limiting to hide cadence loss. A basic frame-delivery pass does not establish
scheduled presentation. Local marker latency excludes WAN/SFU and physical
scanout; see [measurement limits](benchmarks.md).

## Chromium playback and OpenH264 acquisition

Chromium playback/WebCodecs and Discord's native call engine are separate paths.
On the retained Electron 46/Chromium 158, AMD RX 7900 GRE/Mesa 26.2.4 matrix:

| Codec            | Forced software playback | Default AMD playback                 |
| ---------------- | ------------------------ | ------------------------------------ |
| H.264 8-bit      | External OpenH264        | VA-API                               |
| VP8 8-bit        | libvpx                   | libvpx; this driver lacks VP8 decode |
| VP9 8/10-bit     | libvpx                   | VA-API                               |
| AV1 8/10-bit     | dav1d                    | VA-API                               |
| HEVC Main/Main10 | Unsupported              | VA-API                               |

All 25 cases matched expected outcomes: 22 playback passes, three unsupported
software-HEVC failures. Clips were synthetic 1280×720/30 with drain/seek checks;
renderer Seccomp=2/NoNewPrivs=1 remained enabled. Evidence:
[playback inputs/results](../evidence/codec-support.json). This does not qualify
NVIDIA, a native Discord call, arbitrary chroma/bit depths or hardware encoding.
`VaapiOnNvidiaGPUs` only permits the device path with a suitable driver; enabling
AcceleratedVideoEncoder is not a playback fix and reproduced GPU encoder crashes
on this AMD setup. Native VP9 absence does not contradict Chromium VP9 playback.

The imported FFmpeg decoder dynamically loads OpenH264 after sandbox startup;
late loading failed despite a working built-in Chromium software encoder. The
launcher maps the exact verified decoder library before sandbox startup, preserving
hardware decode and user loader entries. Restore the original loader environment
on relaunch/recovery so disabling the codec removes the preload. Preserve the
lexical library name, including symlink names; unrepresentable paths fail clearly.
Never ship `--no-sandbox` as the repair.

`integration/updater/openh264.py` downloads the checksum/size-pinned Cisco archive
from `openh264.json` directly to the user's managed `dpe-codecs/openh264/` cache,
separate from Discord's replaceable asset cache. Lock preparation, bound extraction,
verify compressed and uncompressed bytes and publish atomically/fsynced. Disabled
resolved flags, recovery or a custom user-managed path do not trigger acquisition.
Explicit offline mode needs an intact verified cache; online Quickstart still
prepares a missing codec before launch. No codec binary goes in packages; retain
the provider notice and [upstream licenses](../../THIRD_PARTY.md). License grants
and codec patent rights are distinct; this documentation makes no new codec
redistribution authorization. The private minimal FFmpeg VAAPI backend contains
no GPL/nonfree components or software HEVC codec; preserve its source/rebuild
materials and notices when updating it.

`check_software_codecs.py` and `check_graphics.py --launcher-codecs --strict-codecs`
exercise actual decoder initialization and sandbox state, not capability queries.
Acquisition/offline/corrupt-cache controls and six required software/native codec
cases are retained in `out/validation-storage/openh264-reliability/` and
[software-codec evidence](../evidence/software-codecs.json).
