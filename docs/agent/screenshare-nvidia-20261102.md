# NVIDIA screenshare compatibility review, 2 November 2026

This is a source and ABI review. No NVIDIA hardware was available, and no
NVIDIA capture, encoding, decoding or load test was run. The review supports
preserving the existing native NVENC paths and adding regression coverage;
it does not establish that NVIDIA performance matches the tested AMD system.

## Native routing and workaround boundaries

The reviewed native module SHA-256 is
`72096efc87a407b975e9913477d387e2de9a1f1028575c1f97a22365bacb007c`.
Its `encoding/ffi.h` DWARF and `cc_encoder_create` jump table establish:

| Encoder type                       | Value   | Implemented Linux path                        |
| ---------------------------------- | ------- | --------------------------------------------- |
| `NvidiaCuda`                       | 0       | `CudaVideoEncoder::new`                       |
| `NvidiaVulkan`                     | 10      | NVIDIA `VulkanVideoEncoder::new`              |
| `AmdVaapi`                         | 5       | VAAPI AMD surface backend                     |
| `VulkanVaapi`                      | 6       | VAAPI Vulkan surface backend, including Intel |
| `NvidiaDx11`, `Intel`, `IntelDx11` | 1, 3, 4 | Unsupported branch in this Linux binary       |

`FfiGpuVendor` is NVIDIA 0, AMD 1, Intel 2, Windows Media Foundation 3,
unknown 4. AV1 is codec 2. The dispatch table is at `0x190a9e0` and the
constructor at `0xe2a4d0`; retained evidence is
`out/nvidia-research-20261102/native-{enums.txt,create-dispatch.json}`.

`integration/video/encoder.c` deliberately enters the AV1 sequence/geometry
guard only for types 5 and 6. The AMD padding restriction additionally requires
the actual selected encoder vendor to be AMD and the reviewed VCN4 device
classification. Types 0 and 10 must continue through unchanged, including on
a computer that also contains an AMD VCN4 GPU. The AV1 sequence-header hook
targets only the two reviewed VAAPI constructor calls, not NVENC's headers.
Do not apply the AMD 64×16 restriction or software-generated AV1 level policy
to NVENC.

The expanded [Rust regression](../../native/bridge/src/encoder_guard_tests.rs) now covers
NVENC types 0 and 10 with AV1, the VAAPI hook installed and AMD VCN4 present.
The focused checks passed: creation does not require a VAAPI header, and native
arguments, rate updates and creation failures pass through unchanged. This
validates the adapter boundary, not driver operation. The native binary contains
NVENC capability queries, separate CUDA/Vulkan surface implementations and
`NativeEncoder::reconfigure_encoder`; replacing them is not justified by
the AMD bug.

## Capability and geometry decisions

AV1 is not a universal NVIDIA capability. NVIDIA's current architecture table
lists it for Ada and Blackwell, but not Ampere/Turing. A GPU name or Vulkan
support alone must not make AV1 eligible. [NVIDIA application note][nv-note]

OBS opens an encode session on each CUDA adapter, enumerates codec GUIDs and
queries per-codec capabilities, including dimensions and dynamic bitrate
support. Its probe also keeps per-device results, even though the overall
summary takes maxima across devices. A future DPE diagnostic should distinguish
the selected encoder/device from a machine-wide capability summary.
[OBS capability probe][obs-probe]

The existing `codecs.cjs` preparation barrier and native/SFU capability
intersection are appropriate for NVIDIA too: wait for discovery, retain
explicit exclusions and genuine initialization fallback, and do not force AV1
on an older card. Encode and decode capabilities remain separate. This review
does not prove the installed Chromium receive path supports NVIDIA hardware
decoding merely because NVENC works.

GSR rounds NVENC output to even dimensions and clamps its own minimum to
128×128. That is an application policy, not evidence that every NVIDIA
codec/device has that exact minimum. Preserve visible resolution separately
from allocation pitch and query supported limits; do not replace the corrected
even-dimension GPU capture pool with universal 64-pixel visible padding.
[GSR NVENC implementation][gsr-nvenc]

## Latency and reconfiguration

NVIDIA recommends low/ultra-low latency tuning with CBR for interactive use.
Linux uses synchronous NVENC completion APIs; an asynchronous application
pipeline still needs its own worker and bounded buffers. Bitrate, FPS and
resolution can be reconfigured, but resolution must remain within the maximum
dimensions declared at creation. GOP structure, bit depth and chroma format
cannot be changed by that operation. A resolution change should request an
IDR; resetting rate control is a separate choice. Preserve successful native
reconfiguration and its failure handling instead of rebuilding the encoder
for every network-rate update. [NVENC programming guide][nv-guide]

Recorder defaults are not a low-latency call preset. OBS sizes a frame queue
from B-frame/lookahead requirements and sets output delay to buffer count
minus one; its CBR configuration uses a one-second VBV. GSR selects low-latency
tuning, but its quality mode adds P6 and full-resolution multipass. These are
useful comparison points, not settings to copy over Discord's congestion
controller. Do not force P7, lookahead, multipass, B-frames or CBR padding to
make reported bitrate look higher. Preserve the current native preset until
an NVIDIA run demonstrates a useful latency/quality tradeoff.
[OBS encoder][obs-encoder], [GSR codec setup][gsr-codec]

## GPU buffers, synchronization and capture

OBS's Linux texture route registers GL planes, maps them for each frame,
copies Y/UV into a CUDA encoder surface, unmaps them, then maps that surface
for NVENC. GSR also copies its converted textures into CUDA frame storage
and explicitly synchronizes the CUDA stream. Neither source establishes that
porting its backend would remove a copy from Discord's existing Vulkan/NVENC
path. [OBS texture path][obs-gl], [GSR NVENC implementation][gsr-nvenc]

Do not copy GSR's persistent mapping literally: the reviewed implementation
maps GL resources during setup and unmaps during teardown. CUDA's contract
requires graphics not to access a resource while CUDA has it mapped, and its
map/unmap operations provide ordering guarantees. A DPE interop implementation
would need an explicit ownership/completion protocol, checked import/copy
results and matching graphics/CUDA devices. This is a source-review concern,
not a reproduced GSR or Discord failure. [CUDA graphics interoperability][cuda-map]

OBS derives its texture-path CUDA device from the current GL context and falls
back when that context is incompatible. Its optional cross-GPU texture route
can still transfer through host memory. Consequently, "GPU texture" must not
be presented as "zero-copy", and a hybrid laptop needs separate validation.
[OBS CUDA device selection][obs-cuda], [OBS route selection][obs-encoder]

The existing DPE pacing hook conservatively bypasses buffers carrying explicit
sync or unfamiliar metadata. Keep that behavior: skipping a producer's release
timeline can deadlock or reuse an unfinished image. Preserve original
PipeWire formats/modifiers, acquire/release handling and SHM fallback. NVIDIA
explicit-sync streams may therefore receive less pacing benefit than the AMD
implicit-sync tests; that is an unmeasured performance limit, not a reason to
discard synchronization. The
[Mutter 4563 investigation](screenshare-native-gpu-20261102.md) covers the
compositor-side NVIDIA stale-frame defect and its proposed fixes.

NVENC is an encoder, not a replacement capture API. Keep portal window/monitor
selection as the supported default. NvFBC, KMS and injected game capture have
different platform, permission and source-isolation requirements; see the
[capture-method comparison](capture-methods-20261102.md).

## What remains to verify

A hardware check should cover H.264 on an older NVENC card, AV1 on a supporting
card, genuine AV1 rejection, 1080p and odd/Source dimensions, resize plus
bitrate/FPS recovery, explicit-sync PipeWire, and a hybrid GPU configuration.
Record the actual encoder, selected device, decoded dimensions, buffer/copy
path and fallback reason. Reuse the bounded synthetic harness rather than
running a broad recorder benchmark. No production NVIDIA knob change is
recommended from this review alone.

## Source provenance

OBS master resolved to `65c320b46e1af5c7a555161aea6182cdf7a8c524`; GSR HEAD and
tag 6.1.6 both resolved to `099a55b905a8599b4cc10b7afe6c911f9c1319c7`.
The previously verified GSR archive is retained in the
[capture research provenance](capture-methods-20261102.md#source-provenance-and-limits).
`out/nvidia-research-20261102/sources.json` records newly fetched OBS source
URLs and SHA-256 hashes. NVIDIA documentation was checked on the research
date at Video Codec SDK 13.1 and CUDA Driver API 13.4; these documentation
links are versioned where available, not a minimum driver requirement.

[nv-note]: https://docs.nvidia.com/video-technologies/video-codec-sdk/13.1/nvenc-application-note/index.html
[nv-guide]: https://docs.nvidia.com/video-technologies/video-codec-sdk/13.1/nvenc-video-encoder-api-prog-guide/index.html
[cuda-map]: https://docs.nvidia.com/cuda/cuda-driver-api/cuda_driver_api/group__CUDA__GRAPHICS.html
[obs-probe]: https://github.com/obsproject/obs-studio/blob/65c320b46e1af5c7a555161aea6182cdf7a8c524/plugins/obs-nvenc/obs-nvenc-test/obs-nvenc-test.cpp
[obs-encoder]: https://github.com/obsproject/obs-studio/blob/65c320b46e1af5c7a555161aea6182cdf7a8c524/plugins/obs-nvenc/nvenc.c
[obs-gl]: https://github.com/obsproject/obs-studio/blob/65c320b46e1af5c7a555161aea6182cdf7a8c524/plugins/obs-nvenc/nvenc-opengl.c
[obs-cuda]: https://github.com/obsproject/obs-studio/blob/65c320b46e1af5c7a555161aea6182cdf7a8c524/plugins/obs-nvenc/nvenc-cuda.c
[gsr-nvenc]: https://git.dec05eba.com/gpu-screen-recorder/tree/src/encoder/video/nvenc.c?id=099a55b905a8599b4cc10b7afe6c911f9c1319c7
[gsr-codec]: https://git.dec05eba.com/gpu-screen-recorder/tree/src/recorder/video_codec.c?id=099a55b905a8599b4cc10b7afe6c911f9c1319c7
