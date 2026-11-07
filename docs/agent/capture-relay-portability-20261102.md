# Game-buffer relay portability review — 2026-11-02

This is a source/API review of the current DPE relay, not NVIDIA or Intel hardware
validation. Available hardware evidence belongs to the AMD runs and their exact
candidate builds. No driver installation, GPU test, load test or Discord launch
was performed for this review. Passing helper preflight does not establish that
Discord can import, encode and release every game buffer.

Reviewed implementation: `vkcapture-source.c`, `vkcapture-relay.c`,
`vkcapture-gpu.c`, `vkcapture-gl-sync.c`, the packaged OBS hook patch, broker and
capture controller. Native release behavior is documented in the
[capture synchronization review](screenshare-sync-20261102.md).

## Allocation and GPU identity

The direct hook requests a real linear allocation; it does not relabel an unknown
modifier as linear. The GL route uses dedicated Vulkan memory, exports an opaque
FD for GL and a DMA-BUF for the consumer, and imports the texture using the matching
GL tiling mode. `DRM_FORMAT_MOD_LINEAR` is vendor-independent, but exportable,
renderable and importable combinations still depend on format, usage and driver.
It can also sacrifice compression or tiled-memory efficiency. Fewer copies do not
guarantee lower total GPU cost. See the [Vulkan modifier specification][modifier]
and [external memory feature definitions][memory-features].

The DIRECT GL allocator now queries `VkPhysicalDeviceExternalImageFormatInfo`
and `VkExternalImageFormatProperties` for the exact linear format with both
OPAQUE_FD and DMA_BUF handles before image creation. Both queries must report
EXPORTABLE and compatibility with both handle types, and support the actual
extent, mip/layer count and samples. Its dedicated allocation accommodates
DEDICATED_ONLY. Unsupported combinations trigger the initial export fallback;
these guards are capability checks, not NVIDIA or Intel hardware validation.
[External image queries][image-query]; [handle compatibility][memory-properties].

The helper chooses a render node matching the hook's device UUID; direct hooks
reject device mismatch. This is stronger than choosing a vendor name or the first
render node. However, it verifies **game ↔ helper**, not Discord's independently
chosen Vulkan device or hardware encoder. Hybrid Intel/NVIDIA or Intel/AMD systems
can still fail at the native import boundary. Cross-device DMA-BUF transport and
an eventual copy path are not established merely by the ability to pass the FD.
GL device groups and mixed driver implementations need additional scrutiny; the
optional GL semaphore bridge checks both device and driver UUIDs. These checks
follow the [GL external-object identity model][gl-objects] and
[Vulkan device identifiers][identity].

## Synchronization and ownership

The relay follows PipeWire's explicit-sync contract: image planes followed by two
SyncObj descriptors and mandatory SyncTimeline metadata. A separate reusable
binary syncobj imports the hook's sync_file, then transfers its fence into the
timeline acquire point. It does not replace the timeline itself or wait for the
game on the helper CPU. The native consumer waits before reading. Hook reuse
requires both the consumer's GPU release point and successful original acquire
fence status; an early PipeWire buffer return is insufficient. These match the
[PipeWire protocol][pipewire] and [DRM syncobj model][syncobj].

The preflight exercises timeline support, sync_file export/import, transfer,
future-point Eventfd notification and a zero-timeout wait on a signaled fence.
This detects missing APIs without GPU rendering. Linux 6.6 contains the Eventfd
interface, but API presence is not proof of all correctness fixes. NVIDIA's
590.44.01 documentation requires the WAIT_AVAILABLE and NULL-fence Eventfd fixes
present in 6.8 or appropriate backports. A successful one-shot preflight does not
prove those race fixes are installed. [Kernel implementation][syncobj];
[NVIDIA explicit-sync requirements][nvidia-sync].

Correct timing also does not by itself establish cross-API image layouts or queue
ownership. The Vulkan hook releases to EXTERNAL/GENERAL and reacquires before
writing. The optional GL semaphore bridge exports GENERAL and discards prior
contents for a complete overwrite on reuse. The default GL/EGL completion-fence
path does not provide the same explicit GL semaphore/layout declaration. Keep
these paths distinct when interpreting AMD results or projecting them onto other
drivers. The [GL external-object specification][gl-objects] and
[Vulkan synchronization rules][vk-sync] cover these separate requirements.

## Expected vendor behavior

| Configuration                                                            | Source-based expectation                                                           | Unverified boundary                                                                                             |
| ------------------------------------------------------------------------ | ---------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| Intel with i915 or xe, matching helper GPU                               | Kernel drivers expose timeline syncobjs; capability-driven relay is plausible.     | Exact linear allocation, GL/Vulkan interop, native import and release still need hardware validation.           |
| NVIDIA with working GBM/EGL/Vulkan, matching helper GPU and fixed kernel | The documented APIs offer a plausible route; runtime queries must decide.          | No claim that every NVIDIA generation/driver accepts the two-handle linear allocation or native texture import. |
| Mixed GPUs or drivers                                                    | Keep strict identity checks and reject unsupported import.                         | No automatic device migration or proven cross-GPU fast path.                                                    |
| Missing required sync support                                            | Automatic handoff selects the existing copy path before requesting direct storage. | Copy still needs its own EGL/GBM/native import support.                                                         |

Intel references: upstream [i915][i915] and [xe][xe] advertise
`DRIVER_SYNCOBJ_TIMELINE`. That establishes the kernel primitive, not an encoder
generation or codec claim. NVENC and Intel encoding remain separate from capture;
see the [NVIDIA encoder review](screenshare-nvidia-20261102.md) and
[Intel encoder review](screenshare-intel-20261102.md).

## Actual automatic fallback boundary

The broker defaults handoff to `auto`, while older loaded hooks without the direct
capability use copy. For current hooks, automatic fallback is implemented only for:

1. Synchronization preflight failure before DIRECT control:
   `handoffFallback="synchronization-unavailable"`.
2. Initial hook `UNSUPPORTED`, before any texture generation/native node:
   `handoffFallback="export-unavailable"`.

Explicit `relay` selection fails on those conditions. A helper EGL import failure,
unsupported HDR/format, native format/modifier intersection rejection, mandatory
SyncTimeline negotiation failure, buffer-allocation failure, or a failure after
publication does **not** seamlessly retry copy. The producer fails where it
receives an error; the controller ends the selection/session. Native rejection
need not produce a useful producer-side error. A selected source that never starts
has a 30-second controller timeout, not a negotiation recovery loop.

`ready` is emitted when the node reaches PAUSED so Discord can connect; this
precedes native format negotiation. Its `handoff` reports the producer's selected
path, not confirmed outgoing frames, successful hardware encoding or successful
consumer release. Those need the separate running-stream diagnostics.

Do not add a stop/start retry merely by switching CONTROL's DIRECT bit. The hook
rejects changing mode while active. Furthermore, its STOP packet also signals
ordinary resize retirement: it is not an unambiguous acknowledgement of a stop
command. A queued STOP(old generation)/TEXTURE(new generation) can race such a
retry. Reliable recovery needs an explicit quiescence acknowledgement, preserved
old-buffer ownership and an agreed native-node reconnect/renegotiation contract.
That protocol change was deferred; no speculative retry was added in this review.

## OBS and GPU Screen Recorder comparison

[OBS's PipeWire consumer][obs-pw] also negotiates SyncTimeline, waits for acquisition
and manages release separately from dequeuing. [OBS vkcapture's GL allocator][obs-gl]
is the upstream basis for the Vulkan/GL external-memory route, not proof that the
additional Discord consumer accepts its output on every GPU.

The reviewed GSR snapshot `099a55b` imports KMS DMA-BUFs into EGL and its VAAPI path
exports encoder surfaces as EGL planes for color conversion. Its modifier retry
and vendor-specific handling solve its own pipeline. They do not justify dropping
DPE's modifier checks, copying its fallback policy blindly, or treating GSR's
NVENC/VAAPI success as verification of Discord's importer. The retained source was
read locally because the upstream cgit pages were unavailable to the web reader.
[GSR KMS source][gsr-kms]; [GSR VAAPI source][gsr-vaapi].

[modifier]: https://docs.vulkan.org/refpages/latest/refpages/source/VK_EXT_image_drm_format_modifier.html
[memory-features]: https://docs.vulkan.org/refpages/latest/refpages/source/VkExternalMemoryFeatureFlagBits.html
[image-query]: https://docs.vulkan.org/refpages/latest/refpages/source/VkPhysicalDeviceExternalImageFormatInfo.html
[memory-properties]: https://docs.vulkan.org/refpages/latest/refpages/source/VkExternalMemoryProperties.html
[gl-objects]: https://registry.khronos.org/OpenGL/extensions/EXT/EXT_external_objects.txt
[identity]: https://docs.vulkan.org/refpages/latest/refpages/source/VkPhysicalDeviceIDProperties.html
[pipewire]: https://docs.pipewire.org/1.2/page_dma_buf.html
[syncobj]: https://github.com/torvalds/linux/blob/v6.6/drivers/gpu/drm/drm_syncobj.c
[nvidia-sync]: https://download.nvidia.com/XFree86/Linux-x86_64/590.44.01/README/wayland-issues.html
[vk-sync]: https://docs.vulkan.org/spec/latest/chapters/synchronization.html#synchronization-queue-transfers
[i915]: https://github.com/torvalds/linux/blob/v6.12/drivers/gpu/drm/i915/i915_driver.c
[xe]: https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/xe/xe_device.c
[obs-pw]: https://github.com/obsproject/obs-studio/blob/master/plugins/linux-pipewire/pipewire.c
[obs-gl]: https://github.com/nowrep/obs-vkcapture/blob/8a2c43295fae2ede8ffd382a1581281ceb6153c0/src/glinject.c
[gsr-kms]: https://git.dec05eba.com/gpu-screen-recorder/tree/src/capture/kms.c?id=099a55b
[gsr-vaapi]: https://git.dec05eba.com/gpu-screen-recorder/tree/src/encoder/video/vaapi.c?id=099a55b
