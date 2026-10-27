# Linux Chromium flag research, October 2026

This is the deeper follow-up to [launch presets](launch-presets.md). It checks
definitions, consumers, platform guards and generated build flags, then tests
selected interactions on a real Wayland GPU. A recognized switch, an enabled GPU
status and an actually used media/compositor path are three different findings.

## Version and evidence boundary

Inspected the assembled Chromium source at
`b12138756573818940df44df8f8aba7b4238f7d8` in the retained prerelease build tree.
Tested the verified Electron **46.0.0-alpha.1 / Chromium 158.0.8114.0** binary on
Arch, niri 26.04-214-ge9804c1c, RX 7900 GRE, Mesa 26.2.4 and libva 2.24.1.
No runtime compilation or installed-profile changes were needed.

[Evidence](../evidence/linux-gpu-flags.json) records source and generated-header
hashes, exact test arguments, report hashes and observed decoders. Upstream links
below are navigation aids, not a promise that `main` still matches this binary.
These findings must be rechecked when the runtime changes.

Generated headers confirm VA-API, Vulkan, Skia/Dawn, FFmpeg, dav1d, libvpx,
OpenH264 and platform HEVC decoding are compiled in. V4L2 codecs, the out-of-process
video decoder, platform HEVC encoding and the validating GL command decoder are
not. A feature flag cannot add a missing implementation to a binary.

Balanced remains the recommended starting point. None of this research establishes
a universal faster or lower-power bundle. Experimental controls remain off.

## Active rendering and media controls

Feature names in this table go inside `--enable-features=` or
`--disable-features=`. Names starting with `--` are ordinary switches.

| Control                                                                     | What the inspected Linux build actually does                                                                                                                                                                                                                 |
| --------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `AcceleratedVideoDecoder`                                                   | VA-API build default is enabled. The C++ identifier is `kAcceleratedVideoDecodeLinux`, but the command-line feature name is **AcceleratedVideoDecoder**.                                                                                                     |
| `AcceleratedVideoDecodeLinuxGL`                                             | Enabled by default; permits Linux platform decoding with the GL compositor. Does not install drivers or guarantee a codec/profile.                                                                                                                           |
| `AcceleratedVideoEncoder`                                                   | Disabled by default. Enables Chromium's Linux encoder, independently of decoding. H.264 WebCodecs hardware encoding crashed the local GPU.                                                                                                                   |
| `AcceleratedVideoDecodeLinuxZeroCopyGL`                                     | Enabled by default. Adds NV12/P010 as preferred renderable formats when the import capabilities permit them; AR24/BGR4 fallback remains. Does not prove end-to-end copy elimination.                                                                         |
| `--disable-accelerated-video-decode` / `--disable-accelerated-video-encode` | Separate GPU-preference kill switches. Explicitly enabling a feature does not override every downstream disable or hardware capability check.                                                                                                                |
| `--use-gl=angle --use-angle=gl`                                             | Selects ANGLE's OpenGL backend. Can be useful to isolate a Vulkan regression; not itself a decoding feature.                                                                                                                                                 |
| `--use-gl=angle --use-angle=vulkan`                                         | Selects ANGLE's Vulkan backend. ANGLE/WebGL backend selection alone does not mean the Chromium compositor and video importer also use Vulkan.                                                                                                                |
| `Vulkan,DefaultANGLEVulkan,VulkanFromANGLE`                                 | Off by default on this Linux build. The catalog's Vulkan option requests the compositor/backend/queue-sharing paths together. Linux VA-API then has additional vendor gates described below.                                                                 |
| `VaapiIgnoreDriverChecks`                                                   | In the inspected source, the consumer bypasses the **non-Intel Vulkan decoder vendor rejection**. It does not bypass all checks, including the older-Intel version rejection.                                                                                |
| `VaapiOnNvidiaGPUs`                                                         | Allows opening NVIDIA DRM devices that the VA-API wrapper otherwise skips on x64. Does not provide an NVENC implementation or replace `nvidia-vaapi-driver`.                                                                                                 |
| `--enable-gpu-rasterization`                                                | Forces rasterization status before the rasterization blocklist check; this is itself a targeted protection bypass. `--disable-gpu-rasterization` wins if both are present.                                                                                   |
| `--enable-zero-copy`                                                        | Chooses zero-copy raster buffers on the CPU-raster path. `GpuRasterBufferProvider` is selected first when GPU rasterization is active. Combining the two switches does not stack two raster optimizations.                                                   |
| `--enable-native-gpu-memory-buffers`                                        | Enables additional native-buffer handling, including CPU-mappable DMA-BUF configurations. Format/usage support still applies.                                                                                                                                |
| `--enable-gpu-memory-buffer-compositor-resources`                           | Overrides a default that is false on Linux. Separate from video-frame buffers and zero-copy decoding; no general performance claim or automatic preset inclusion.                                                                                            |
| `--enable-gpu-memory-buffer-video-frames`                                   | No additional enabling effect in the Linux renderer. The normal path already enables video buffers when GPU compositing is available unless the **disable** switch is present. Retired from the editor.                                                      |
| `--disable-gpu-memory-buffer-video-frames`                                  | An actual diagnostic change on Linux. Tested with forced encoding: H.264 encoding still crashed; this was not a workaround.                                                                                                                                  |
| `--enable-hardware-overlays=single-fullscreen,single-on-top,underlay`       | Requests candidate strategies. These are already the default strategies when platform overlay support is advertised. Cannot supply missing Wayland protocols or guarantee direct scanout.                                                                    |
| `--double-buffer-compositing`                                               | The buffer-queue output device changes from three buffers to two and reduces pending swaps. Added as an opt-in experiment after a passing smoke test. Lower queue depth can trade queued latency for missed-frame sensitivity; no latency gain was measured. |
| `--disable-frame-rate-limit`                                                | Also disables GPU vsync. Rendering more frames than can be presented is not evidence of smoother delivery or lower latency.                                                                                                                                  |
| `--num-raster-threads=N`                                                    | Clamped to **1–4** in this version, normally selected from CPU count. Recipes using 8/16 do not obtain that many raster workers here.                                                                                                                        |
| `--use-cmd-decoder=passthrough`                                             | Redundant for this ANGLE build: the validating decoder is compiled out. Requesting `validating` is ignored with a warning.                                                                                                                                   |
| `--ignore-gpu-blocklist`                                                    | Broad GPU protection bypass. Distinct from VA-API vendor checks and driver bug workarounds.                                                                                                                                                                  |
| `SkipVulkanBlocklist`                                                       | The inspected use in `IsVulkanSupported()` is in the Android branch. It is not added to the Linux bypass bundle merely because the feature is defined.                                                                                                       |
| `--disable-gpu-driver-bug-workarounds`                                      | Disables driver-specific fixes; spelling is plural. No evidence of a general speed or efficiency improvement.                                                                                                                                                |

Primary source paths: [media features][media], [Linux decoder implementation][decoder],
[VA-API device selection][vaapi], [renderer codec/buffer setup][renderer],
[GPU preferences][preferences], [GPU feature evaluation][gpu-util],
[raster-provider selection][raster], [compositor configuration][compositor],
[native-buffer formats][pixmap], [ANGLE/Vulkan features][gl],
[command decoder selection][gl-utils], [overlay strategies][overlays],
[buffer queue][queue] and [Viz switches][viz-switches].

## Dependencies that changed the conclusions

### Graphite is explicitly blocked through the feature flag

`IsSkiaGraphiteSupportedByDevice()` returns false on Linux. Therefore
`--enable-features=SkiaGraphite` does not activate Graphite, even though Dawn is
compiled in. The earlier passing probe, which reported `skia_graphite: disabled_off`,
was an inactive-feature result.

The stronger test-only `--enable-skia-graphite` bypasses that platform guard.
Testing it with `--skia-graphite-dawn-backend=vulkan` produced a GPU crash and no
WebGL context before playback. This is evidence against **that combination on
this machine**, not a test of every Graphite backend. The ineffective checkbox is
retired; saved selections receive a notice and are removed from the resolved
configuration. They are never upgraded to the stronger switch. The subsequent
**Force Skia Graphite (WIP)** control explicitly requests the tested
`--enable-skia-graphite --skia-graphite-dawn-backend=vulkan` combination under a new
`graphiteForce` key. It starts off; selecting the acceleration bypass does not
select it. The manual editor also accepts testing switches. See [Graphite platform guard][graphite].

### Vulkan playback has a vendor gate beyond Vulkan rendering

The Linux decoder requires a Vulkan compositor, `VulkanFromANGLE`,
`DefaultANGLEVulkan` and available Vulkan device information. Intel has an explicit
minimum driver-version comparison. Other vendors return no VA-API decoder unless
`VaapiIgnoreDriverChecks` is enabled.

The previous AMD Vulkan probe failed H.264 playback with `kUnsupportedConfig`.
Adding only the separate vendor-check bypass to the same Vulkan bundle produced
actual Vulkan WebGL rendering **and `VaapiVideoDecoder` H.264 playback**, with no
GPU exit in the short test. That explains the earlier failure; it does not justify
silently bypassing the gate for AMD users. The checkbox description now names its
specific purpose. See [actual decoder selection][decoder].

### Overlay delegation requires more than a feature toggle

`WaylandOverlayDelegation` is disabled by default. The inspected connection requires
the fractional-scale manager to be absent and the single-pixel-buffer manager to
be present; Ozone additionally requires `wp_viewporter` to advertise overlay support.
This is a protocol-capability condition, not merely whether the monitor happens
to use a fractional scale today. See [Wayland connection][wayland] and
[platform properties][ozone].

On this niri session, enabling delegation and the overlay strategies played video
successfully but created no subsurfaces. A second probe disabled
`WaylandFractionalScaleV1`: the fractional-scale binding disappeared, but niri still
did not advertise `wp_single_pixel_buffer_manager_v1`, and no subsurfaces appeared.
Neither passing run proves overlay promotion or reduced composition work. Disabling
fractional scaling solely to chase overlays is not a default recommendation.

### Explicit synchronization is already negotiated

The connection binds `wp_linux_drm_syncobj_manager_v1` when the compositor advertises
it and the kernel is at least **6.11**; the source comment cites fixes after the
original 6.6 ioctl implementation. There is no `WaylandLinuxDrmSyncobj` feature gate
in this path. Both overlay probes bound the protocol and issued acquire/release
point requests without such a flag. This confirms protocol use in those runs,
not that every frame or driver synchronization issue is solved. See [Wayland connection][wayland].

## Wayland, capture and scheduling recipes

Electron's [March 2026 Wayland explanation][electron-wayland] confirms native
Wayland support out of the box from 38.2. The app's explicit session selection and
Xwayland fallback remain useful controls; old Ozone enablement bundles are unnecessary.

| Recipe                                                                           | Current interpretation                                                                                                                                                                                                       |
| -------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--ozone-platform=wayland` / `x11`                                               | Real backend selection; X11 within a Wayland session uses Xwayland. Test the two independently when diagnosing driver/compositor regressions.                                                                                |
| `UseOzonePlatform`, `--ozone-platform-hint=auto`, `ELECTRON_OZONE_PLATFORM_HINT` | Old enablement/hint recipes; do not reintroduce them into this runtime's presets.                                                                                                                                            |
| `WaylandFractionalScaleV1`                                                       | Enabled by default. Relevant to scaling and the overlay gate, not a generic performance feature to disable.                                                                                                                  |
| `WebRTCPipeWireCapturer`                                                         | Old gate absent from the inspected capture implementation. `content/public/browser/desktop_capture.cc` enables PipeWire options when compiled with `WEBRTC_USE_PIPEWIRE`. Portal availability remains external.              |
| `WebRtcPipeWireCamera`                                                           | A different, existing feature, disabled by default, for camera enumeration/capture. It does not enable screen sharing. No physical-camera test was performed.                                                                |
| `webrtc-hw-encoding`, `webrtc-hw-decoding`                                       | The actual **feature strings** are lower-case and hyphenated; both default on in this Linux build. Capitalized `WebRtcHWEncoding`/`WebRtcHWDecoding` in copied feature lists are not the strings used by `FeatureList`.      |
| `ZeroCopyDesktopCapture`                                                         | Existing feature, disabled by default, used by the shared desktop-capture path. Its consumer still reserves a CPU I420 output buffer and wraps that memory. The name does not promise PipeWire DMA-BUF-to-encoder zero-copy. |
| `--disable-background-timer-throttling`                                          | Keeps background timers active; can spend more CPU/battery. Does not accelerate GPU rendering or raise a display refresh rate.                                                                                               |
| `--disable-renderer-backgrounding`                                               | Changes renderer background scheduling. Responsive keeps it explicit; no measured frame-time gain is claimed.                                                                                                                |
| `IntensiveWakeUpThrottling`                                                      | Already enabled by default. Efficiency restates that policy, not a new battery-saving algorithm. Active media and app/window policies affect throttling.                                                                     |
| `--disable-backgrounding-occluded-windows`                                       | Existing test switch that treats an `OCCLUDED` WebContents as visible. Distinct from background/minimized states and not a general Wayland occlusion fix.                                                                    |
| `CalculateNativeWinOcclusion`                                                    | Windows-only definition and consumer. Disabling it on Linux does not improve frame pacing.                                                                                                                                   |

See [desktop capture options][capture-options], [camera feature][camera],
[WebRTC feature strings][content-features], [desktop frame conversion][capture],
[visibility handling][visibility] and [Windows occlusion feature][ui-features].
Electron's [`backgroundThrottling` preference][web-preferences] can also override
normal background behavior. Our graphics fixture deliberately disables that
preference so short playback tests are reliable; those tests cannot validate
the scheduling presets' power behavior.

Old `VaapiVideoDecodeLinuxGL`, `UseChromeOSDirectVideoDecoder`, `UseSkiaRenderer`,
`CanvasOopRasterization` and `WaylandLinuxDrmSyncobj` recipes did not have active
feature definitions/consumers in the inspected relevant source paths. Some names
survive in comments. `EnableDrDc` is worse than stale: the earlier investigation
reproduced Linux crashes, and the editor rejects it. Unknown names being accepted
without an error is not proof of support.

## GPU-specific interpretation

- **Intel:** use the media driver appropriate for the generation and codec.
  Intel documents differences between full-feature/free-kernel packages, encoding
  entrypoints and HuC-dependent rate control. A generic force-encode switch cannot
  substitute for firmware or codec support. The source's Intel Vulkan allowance
  is not a local Intel test. See [Intel media-driver documentation][intel].
- **AMD/Mesa:** GL plus VA-API already works here. Vulkan plus the explicit vendor
  bypass also passed, while forced encoding failed. Mesa advises that environment
  overrides normally are unnecessary; `MESA_NO_ERROR` removes API checking and
  is not an established Discord optimization. See [Mesa environment variables][mesa].
- **NVIDIA:** current driver documentation describes Chromium-compatible buffer
  export and a GL/VA-API recipe; older blanket claims of incompatibility are too
  broad. This is NVDEC decoding, **not NVENC encoding**. Review the installed
  driver version and actual codec before selectively testing `VaapiOnNvidiaGPUs`
  or a blocklist bypass. The README also still includes an old Ozone hint, so do
  not copy its entire recipe uncritically. See [nvidia-vaapi-driver][nvidia-vaapi].
- **NVIDIA synchronization:** egl-wayland2 documents a targeted
  `__NV_DISABLE_EXPLICIT_SYNC=1` workaround for certain multithreaded surface
  commits. This is a driver-library environment control, not a universal Chromium
  fix or an instruction to turn synchronization off. See [known issues][nvidia-egl].
- **Hybrid GPUs:** renderer and video-device selection can differ. Mesa's
  `DRI_PRIME` changes GL selection/Vulkan enumeration; waking a discrete GPU can
  cost more energy than it saves. Inspect both the renderer and the actual media
  decoder. Environment assignments belong outside the flags textarea. No hybrid
  laptop, Intel, NVIDIA, ARM or virtual GPU was physically tested in this pass.

## Native test results and reproduction

Eleven fresh-profile tests supplement the previous 24-case matrix. Full reports
are under `out/flags-deep-research`; the compact committed evidence preserves
results even if those generated directories are removed.

| New case                                      | Observed result                                                                           |
| --------------------------------------------- | ----------------------------------------------------------------------------------------- |
| Forced Graphite, Dawn Vulkan                  | GPU crash; no WebGL context; playback not reached.                                        |
| Vulkan plus VA-API vendor bypass              | Passed; Vulkan renderer and VA-API H.264 decoder.                                         |
| Forced encode, video buffers disabled         | GPU crash and hardware H.264 encoding error; playback itself used VA-API.                 |
| Overlay strategies plus delegation            | Passed playback; no delegated subsurfaces.                                                |
| Same, fractional-scale feature disabled       | Passed playback; no delegated subsurfaces; required single-pixel-buffer protocol missing. |
| Double-buffer compositing                     | Passed WebGL/playback smoke test; latency and frame pacing not measured.                  |
| H.264 / VP9 / AV1 / HEVC playback, default GL | All four passed with `VaapiVideoDecoder`.                                                 |
| VP8 playback, default GL                      | Passed with `VpxVideoDecoder`, a software decoder.                                        |

Every clip is synthetic 1280×720, 30 fps, two seconds, 8-bit 4:2:0. A pass requires
WebGL pixel readback, at least five delivered video frames, no observed GPU exit,
and the fixture's supported software encoder probes to complete. Most follow-up
runs disabled the optional dynamically loaded OpenH264 encoder rather than loading
its library; software H.264 encoding is therefore not covered by those runs.
The earlier matrix separately tested it with the verified library.

Reproduce a codec or combination with an existing verified distribution:

```sh
uv run python scripts/check_graphics.py /path/to/runtime/electron \
  --output out/av1-check --video-codec av1 \
  --flag=--ozone-platform=wayland \
  --flag=--disable-features=OpenH264SoftwareEncoder

uv run python scripts/check_graphics.py /path/to/runtime/electron \
  --output out/vulkan-vendor-check \
  --flag=--ozone-platform=wayland \
  --flag=--use-gl=angle --flag=--use-angle=vulkan \
  --flag=--enable-features=Vulkan,DefaultANGLEVulkan,VulkanFromANGLE,VaapiIgnoreDriverChecks \
  --flag=--disable-features=OpenH264SoftwareEncoder
```

This extends compatibility coverage, not performance evidence. A performance
recommendation would need repeated baseline/one-change comparisons with the same
video, scale, refresh rate and window visibility; delivered/dropped frames, frame
times, CPU/GPU activity and energy measurements; and confirmation of the actual
decoder/import path. High-refresh/VRR, HDR/10-bit, long playback, uploaded Discord
media, browser WebRTC and Discord's separate native calls remain distinct tests.
No account login, messages, calls, desktop recording or physical-camera use occurred.

The flags-free recovery path remains available before the preset parser starts.
It skips app-supplied launch flags; it cannot uninstall a driver or clear external
driver environment variables. See the short [user instructions](../linux-launcher.md).

[media]: https://chromium.googlesource.com/chromium/src/+/main/media/base/media_switches.cc
[decoder]: https://chromium.googlesource.com/chromium/src/+/main/media/mojo/services/gpu_mojo_media_client_linux.cc
[vaapi]: https://chromium.googlesource.com/chromium/src/+/main/media/gpu/vaapi/vaapi_wrapper.cc
[renderer]: https://chromium.googlesource.com/chromium/src/+/main/content/renderer/render_thread_impl.cc
[preferences]: https://chromium.googlesource.com/chromium/src/+/main/content/public/browser/gpu_utils.cc
[gpu-util]: https://chromium.googlesource.com/chromium/src/+/main/gpu/config/gpu_util.cc
[raster]: https://chromium.googlesource.com/chromium/src/+/main/cc/trees/layer_tree_host_impl.cc
[compositor]: https://chromium.googlesource.com/chromium/src/+/main/content/browser/gpu/compositor_util.cc
[pixmap]: https://chromium.googlesource.com/chromium/src/+/main/ui/gfx/linux/client_native_pixmap_dmabuf.cc
[gl]: https://chromium.googlesource.com/chromium/src/+/main/ui/gl/gl_switches.cc
[gl-utils]: https://chromium.googlesource.com/chromium/src/+/main/ui/gl/gl_utils.cc
[overlays]: https://chromium.googlesource.com/chromium/src/+/main/components/viz/host/renderer_settings_creation.cc
[queue]: https://chromium.googlesource.com/chromium/src/+/main/components/viz/service/display_embedder/skia_output_device_buffer_queue.cc
[viz-switches]: https://chromium.googlesource.com/chromium/src/+/main/components/viz/common/switches.cc
[graphite]: https://chromium.googlesource.com/chromium/src/+/main/gpu/config/gpu_finch_features.cc
[wayland]: https://chromium.googlesource.com/chromium/src/+/main/ui/ozone/platform/wayland/host/wayland_connection.cc
[ozone]: https://chromium.googlesource.com/chromium/src/+/main/ui/ozone/platform/wayland/ozone_platform_wayland.cc
[electron-wayland]: https://www.electronjs.org/blog/tech-talk-wayland
[capture-options]: https://chromium.googlesource.com/chromium/src/+/main/content/public/browser/desktop_capture.cc
[camera]: https://chromium.googlesource.com/chromium/src/+/main/media/capture/capture_switches.cc
[content-features]: https://chromium.googlesource.com/chromium/src/+/main/content/public/common/content_features.cc
[capture]: https://chromium.googlesource.com/chromium/src/+/main/content/browser/media/capture/desktop_capture_device.cc
[visibility]: https://chromium.googlesource.com/chromium/src/+/main/content/browser/web_contents/web_contents_impl.cc
[ui-features]: https://chromium.googlesource.com/chromium/src/+/main/ui/base/ui_base_features.cc
[web-preferences]: https://www.electronjs.org/docs/latest/api/structures/web-preferences
[intel]: https://github.com/intel/media-driver
[mesa]: https://docs.mesa3d.org/envvars.html
[nvidia-vaapi]: https://github.com/elFarto/nvidia-vaapi-driver#chrome
[nvidia-egl]: https://github.com/NVIDIA/egl-wayland2#known-issues
