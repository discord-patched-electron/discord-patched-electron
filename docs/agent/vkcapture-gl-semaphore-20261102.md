# GL semaphore visibility and completion

The direct game relay passes its capture allocation to Discord's native GPU reader
without the helper's second pixel copy. EGL exports completion directly; GLX uses
a small Vulkan semaphore bridge. Both explicitly release texture visibility and
layout. The original backbuffer blit, RGB-to-NV12 conversion and encoding remain.

The bridge is enabled for DIRECT capture when supported. Set
`DPE_GL_SEMAPHORE_SYNC=0` in the **game's** environment only for diagnostic
comparisons; that bypass does not provide the explicit layout contract. Set it to
`1`, or leave it unset, for normal behavior. The copy path remains unchanged.
Rebuild hooks using `scripts/build_vkcapture_backend.py`; Electron does not need a
rebuild. A fresh compile is retained at
`out/vkcapture-gl-sync-cycle-vk-safe-20261102/`. Hardware measurements belong to the
parent capture-pipeline experiment; this note does not assert a measured win.

## Contract

The allocation remains the existing Vulkan-backed, explicit-linear export.

1. Only actual native GPU release permits the existing frame lease to be reused.
   A CPU callback or buffer dequeue is insufficient.
2. GL waits on a previously signaled imported semaphore with texture layout
   `GL_NONE`, corresponding to Vulkan `UNDEFINED`.
3. The original GL blit overwrites the full texture, with scissor disabled. This
   discard acquisition does not assume the native reader's final layout.
4. GL signals completion with the texture in `GENERAL`, making writes visible and
   flushing the command stream.
5. The matching completion `sync_file` is sent through the existing frame packet.

EGL uses one opaque semaphore. The hook signals it once at startup, then each
frame's wait consumes the previous signal and the final GENERAL release signals
it again. An EGL native fence inserted after that release supplies the completion
FD. This route makes no Vulkan queue submissions.

GLX, or EGL without native-fence support, uses a private Vulkan queue. One startup
submission primes the acquire semaphore. Each frame's final submission waits for
GL completion and signals both the exported `sync_file` semaphore and the next
frame's acquire semaphore. This is one empty Vulkan submission per frame, without
pixel copies. Recycling the acquire signal avoids an additional per-frame GL
signal, which itself forces a command flush.

EGL sync status or private Vulkan fences are queried without waiting before
reuse. A busy fence cancels only the newly admitted frame and retains the capture
rate deadline. There is no `WaitForFences`, `QueueWaitIdle` or `DeviceWaitIdle` in
the GL bridge.

Initialization requires actual GL semaphore extensions, matching GL/Vulkan device
and driver UUIDs, opaque-FD semaphore export and a graphics queue. Vulkan
completion additionally requires `SYNC_FD` semaphore export. Failure uses the
initial `UNSUPPORTED` reply so automatic mode can choose copy capture. The strict
UUID check can conservatively reject a driver combination that could interoperate.

Before creating a DIRECT GL export image, query the exact explicit-linear format,
image type, usage and flags for opaque-FD and DMA-BUF export independently. Both
must report `EXPORTABLE` and compatible handle sets containing both types. Check
requested dimensions, mip count, array layers and samples against returned limits.
The existing dedicated allocation supports dedicated-only requirements.

The Vulkan game hook similarly checks DMA-BUF export, explicit linear allocation
and an 8-bit SDR RGBA/BGRA swapchain. It verifies the returned modifier and single
plane before publishing. Complete overwrites transition from `UNDEFINED` with
ignored queue-family indices, then release to external ownership in `GENERAL`.
This handles the first locally allocated image and later native-reader releases
without assuming the reader's final layout.

## Resource lifetime and limits

Successful GL import transfers FD ownership. Pending GPU objects are retained
during teardown; at most four retired generations can accumulate before new direct
initialization is refused. Export errors and device loss do not imply completion.
An EGL fence-creation failure after GL work retains that generation until context
destruction because no completion evidence exists.

GL names are deleted only with their creating context current. Successful context
destruction invalidates retired names, preventing a reused context pointer from
deleting unrelated objects. The pinned upstream hook still frees capture on any
context destruction and GLX window creation. Broader multi-context capture
ownership remains an upstream limitation. Separate Vulkan present/graphics queue
families also remain unverified; the source swapchain ownership logic was not
changed. NVIDIA and Intel hardware checks remain necessary.

## Evidence and checks

- [Khronos external objects](https://registry.khronos.org/OpenGL/extensions/EXT/EXT_external_objects.txt)
  permits a GL-flushed signal before a GL wait, resets the semaphore on wait and
  defines the texture visibility/layout transitions. It does not require the
  signaler and waiter to be different APIs.
- [External object FD import](https://registry.khronos.org/OpenGL/extensions/EXT/EXT_external_objects_fd.txt)
  defines ownership transfer and deliberately omits a GLX `sync_file` mechanism.
- [Vulkan external resource sharing](https://docs.vulkan.org/spec/latest/chapters/resources.html#resources-external-sharing)
  permits discarding previous contents after access ordering is satisfied.
  The actual native GPU-release lease supplies that ordering.
- [Vulkan semaphore FD export](https://docs.vulkan.org/refpages/latest/refpages/source/vkGetSemaphoreFdKHR.html)
  supplies GLX completion, including its already-complete `-1` sentinel.
- [EGL native fences](https://registry.khronos.org/EGL/extensions/ANDROID/EGL_ANDROID_native_fence_sync.txt)
  cover earlier client API commands. Inserting one after GENERAL release includes
  that transition; an EGL fence alone does not specify a Vulkan texture's layout.
- [External memory properties](https://docs.vulkan.org/refpages/latest/refpages/source/VkExternalMemoryProperties.html)
  and [image queries](https://docs.vulkan.org/refpages/latest/refpages/source/VkPhysicalDeviceExternalImageFormatInfo.html)
  define exportability and simultaneous handle compatibility.

`tests/screenshare/vkcapture-gl-sync-check.c` runs the production bridge with fake
driver dispatch under ASan/UBSan. It checks capability rejection, layout/operation
order, backpressure, FD ownership, failure retirement, device loss, wrong-context
cleanup and context-pointer reuse. Repeated cycles verify zero EGL-path Vulkan
submissions and one GLX-path submission per frame after bootstrap. Image-query
fixtures reject unsupported handles, missing exportability, incompatible handles,
excessive dimensions and unsupported samples. These tests never open a GPU.
Both pinned patched hooks compile from a fresh archive.

Do not replace this with `DMA_BUF_IOCTL_EXPORT_SYNC_FILE` immediately after
`glFlush`: Mesa's threaded flush can return before the kernel receives capture
work, so a reservation-fence snapshot can miss the blit. Earlier source evidence
is in [the capture pipeline notes](capture-pipeline-20261102.md).
