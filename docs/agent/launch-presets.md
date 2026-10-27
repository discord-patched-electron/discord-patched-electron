# Launch presets and Linux GPU flag review

Reviewed 27 October 2026 against the locked Electron **46.0.0-alpha.1 / Chromium
158.0.8114.0**, its retained assembled sources, and current upstream documentation.
This replaces the bundled OpenAsar Focus flag lists. The earlier
[playback investigation](video-playback.md) isolates `EnableDrDc` as the original
Linux GPU crash trigger. User instructions are in [launcher settings](../linux-launcher.md).
The [deeper source and runtime audit](linux-gpu-flags.md) supersedes the initial
Graphite/video-buffer descriptions and explains Vulkan and overlay dependencies.

## Defaults and scope

Balanced leaves Chromium's rendering and scheduling choices intact. Responsive
only adds `--disable-background-timer-throttling` and `--disable-renderer-backgrounding`;
it trades background CPU activity for responsiveness, not a higher refresh rate.
Efficiency explicitly enables `IntensiveWakeUpThrottling`, already the default in
this build. Neither label claims a measured battery or frame-time improvement.

Every GPU override, backend override and codec checkbox starts off in every preset.
Unchecked means runtime defaults, not forced software processing. Driver installation,
codec availability, compositor behavior and Discord's separate native voice engine
cannot be configured reliably through a generic Chromium flag bundle.

The old base list disabled background scheduling, media-session behavior and
Windows-only features indiscriminately. Those overrides are gone. We also removed
automatic back/forward-cache policy changes, WebAssembly compilation settings,
iframe policy overrides and power-GPU hints: they are not established video or
Wayland improvements for this app. The old flags remain documented in the earlier
audit, and arbitrary supported switches can be entered manually.

## Current Chromium/Electron findings

| Area               | Finding and implementation choice                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Wayland            | Electron 38 made Ozone selection automatic and removed `ELECTRON_OZONE_PLATFORM_HINT`. Normal launches explicitly select the active session; X11/Xwayland is selectable. No `UseOzonePlatform` or old hint recipes. [Electron 38 release notes](https://www.electronjs.org/blog/electron-38-0).                                                                                                                                                                                                 |
| VA-API decode      | `AcceleratedVideoDecoder` and `AcceleratedVideoDecodeLinuxGL` are enabled by default in this VA-API build. The decode checkbox requests them explicitly without bypassing driver checks. `AcceleratedVideoEncoder` is disabled by default and gets its own checkbox. [Media feature definitions](https://chromium.googlesource.com/chromium/src/+/main/media/base/media_switches.cc).                                                                                                           |
| Zero-copy video    | `AcceleratedVideoDecodeLinuxZeroCopyGL` still exists, in the Linux GPU media client rather than the main media switch file. It is enabled by default, uses compatible NV12/P010 imports, and is different from raster `--enable-zero-copy`. An optional request is exposed. [Linux decoder implementation](https://chromium.googlesource.com/chromium/src/+/main/media/mojo/services/gpu_mojo_media_client_linux.cc).                                                                           |
| Vulkan             | ANGLE Vulkan, `Vulkan`, `DefaultANGLEVulkan` and `VulkanFromANGLE` are an explicit experimental backend. The Linux VA-API Vulkan path checks the GPU vendor/driver; enabling Vulkan can lose working GL decode. `VaapiIgnoreDriverChecks` stays a separate opt-in. [GL features](https://chromium.googlesource.com/chromium/src/+/main/ui/gl/gl_switches.cc), [Linux decoder checks](https://chromium.googlesource.com/chromium/src/+/main/media/mojo/services/gpu_mojo_media_client_linux.cc). |
| Rendering          | GPU rasterization, zero-copy raster uploads, overlays, native buffers and double buffering are separate experiments. Graphite via its feature flag is explicitly blocked on Linux; its stronger testing override crashed. The ineffective Graphite and video-buffer enable checkboxes were retired with saved-selection notices. [GPU features](https://chromium.googlesource.com/chromium/src/+/main/gpu/config/gpu_finch_features.cc).                                                        |
| Wayland protocols  | Fractional scaling is already enabled by default. `WaylandOverlayDelegation` is disabled and exposed separately; allowing subsurfaces does not itself enable delegated composition. No automatic explicit-sync override. [Ozone feature definitions](https://chromium.googlesource.com/chromium/src/+/main/ui/ozone/common/features.cc).                                                                                                                                                        |
| Frame pacing       | `--disable-frame-rate-limit` also disables GPU vsync. It is an explicit experiment, not a smoothness preset; it can increase power draw or tearing. Do not confuse uncapped production with timely presentation by the compositor. [Viz switch definition](https://chromium.googlesource.com/chromium/src/+/main/components/viz/common/switches.cc).                                                                                                                                            |
| Driver protections | `--ignore-gpu-blocklist`, `VaapiIgnoreDriverChecks` and `--disable-gpu-driver-bug-workarounds` are separate opt-ins. The last name is plural. None installs codecs or repairs unsupported drivers. Sandbox-disabling and crash-limit-disabling switches are not presets. [GL switches](https://chromium.googlesource.com/chromium/src/+/main/ui/gl/gl_switches.cc), [VA-API guidance](https://chromium.googlesource.com/chromium/src/+/main/docs/gpu/vaapi.md).                                 |
| Known bad / stale  | `EnableDrDc` explicitly lacks Linux support and reproduced GPU crashes, so the editor rejects it, including parameter/trial forms. `UseSkiaRenderer`, `CanvasOopRasterization`, `TurnOffStreamingMediaCachingOnBattery`, old `VaapiVideoDecodeLinuxGL`, and `WaylandLinuxDrmSyncobj` were not active feature definitions in the inspected pinned GPU/media/Ozone sources. Some survive in comments. Do not infer support from a flag being accepted without error.                              |

The source checkout inspected was the retained build tree at
`prerelease/f6a6a79b20ad975b5e64ad64d45df92dd007d514785f46cd66cd58baed91a390/checkout/src`,
assembled HEAD `b12138756573818940df44df8f8aba7b4238f7d8`. The live upstream links
above can change; repeat the definition and call-site review when runtime pins change.
In particular, broad searches matter: the zero-copy decoder feature is defined
locally in `media/mojo/services/gpu_mojo_media_client_linux.cc`.

## GPU-specific guidance

| Hardware                              | Starting point and optional investigation                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Intel iGPU / Arc                      | Balanced with the appropriate libva driver. Intel's media driver documents supported generations and codec/entrypoint differences; `iHD` is not a substitute for hardware support. Check `vainfo`, actual decoder selection and power use before forcing options. Encoding capability in `vainfo` does not prove Chromium's encoder works. [Intel media driver](https://github.com/intel/media-driver).                                                                                                                                                                                                                                                                                          |
| AMD Radeon / Mesa                     | Balanced with the distribution's working Mesa/libva stack. Here GL/VA-API already decoded H.264 in hardware. A follow-up Vulkan probe worked only with the separate VA-API vendor-check bypass; forced encoding still crashed. Leave Mesa debug/performance variables alone unless testing a specific driver issue. [Mesa environment documentation](https://docs.mesa3d.org/envvars.html).                                                                                                                                                                                                                                                                                                      |
| NVIDIA proprietary driver             | Version-sensitive. Current `nvidia-vaapi-driver` documentation now describes a Chromium-compatible export path and a Chrome recipe; older statements that it never works are too broad. It provides NVDEC decoding, not NVENC encoding. The NVIDIA VA-API option, GL backend and individual driver-check overrides are available separately; no vendor detection silently enables them. `LIBVA_DRIVER_NAME=nvidia`, when needed, belongs in the launch environment, not a flag textarea. [Driver README](https://github.com/elFarto/nvidia-vaapi-driver#chrome).                                                                                                                                 |
| NVIDIA Wayland synchronization        | Keep a compatible driver, compositor and EGL Wayland library. NVIDIA documents a targeted explicit-sync workaround for a multithreaded commit issue in egl-wayland2. That is a diagnostic workaround, not a universal latency/efficiency setting. [NVIDIA egl-wayland2 known issues](https://github.com/NVIDIA/egl-wayland2#known-issues).                                                                                                                                                                                                                                                                                                                                                       |
| Hybrid laptops                        | Prefer the integrated GPU for light workloads if measurements support it. Offloading can keep a discrete GPU awake. Mesa documents `DRI_PRIME`; NVIDIA documents its PRIME environment controls for EGL/Vulkan/GLX. These select devices outside the preset parser. Electron documents `--force_low_power_gpu` and `--force_high_performance_gpu`, but we do not assume they reliably select a Linux device on every driver. [Mesa selection](https://docs.mesa3d.org/envvars.html#dri-prime), [NVIDIA PRIME](https://download.nvidia.com/XFree86/Linux-x86_64/580.76.05/README/primerenderoffload.html), [Electron switches](https://www.electronjs.org/docs/latest/api/command-line-switches). |
| Software renderers / VMs / other GPUs | Retain runtime protections. A blocklist bypass can turn a fallback into a crash. These devices were not available for local validation.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |

NVIDIA documentation is in transition: compare the installed driver release with
the current README, rather than copying all switches from a guide. No Intel,
NVIDIA, hybrid, HDR or variable-refresh hardware was available for this test pass.
There is no cross-GPU benchmark or claim of improved energy consumption.

## Preset implementation and recovery

`integration/launcher/presets.json` is the catalog. The standard-library Python
resolver runs before Electron and also powers the native editor's preview. Saved
settings now live in the channel flags file (see [file storage](flags-file.md)); each preset
retains its own text, codec options, backend and experiments. Legacy `perf`/`battery`
selections map to the new Responsive/Efficiency definitions. Missing selections
start Balanced. Explicit legacy custom flags migrate into Additional flags, with
`EnableDrDc` removed and a review notice. Old automatic GPU overrides do not migrate.

Precedence, low to high: application identity/defaults → selected options → preset
text → Additional flags outside the managed section → original CLI.
Legacy settings and file overrides migrate into the canonical file once.
Feature lists merge by normalized feature name across enable/disable switches;
parameters and trial syntax survive, and the final occurrence wins. Ordinary
switches retain Chromium's last-value handling. Text is never evaluated by a shell.
Values containing spaces or `=` remain one argument. No quotes or environment
assignments are required in textareas/files.

`--ignore-flags` / `DPE_IGNORE_FLAGS=1` is handled in the shell **before reading
settings, flag files or even loading the resolver**. It drops all supplied
arguments, including URLs, for that session. `DPE_FLAGS_MANAGED=1` prevents pinned
OpenAsar from injecting its flags afterward. Essential internal bootstrap/sandbox
plumbing remains; recovery does not disable the GPU or sandbox and does not delete
preferences. It cannot repair an unrelated runtime/driver failure.

The editor can repair invalid preset metadata and Additional flags in recovery.
Invalid flags are identified by field/path; restart is blocked until they are
repaired and saved. Damaged metadata is backed up before replacement.
`DPE_DEFAULT_FLAGS=0` is a narrower advanced control: identity, external files and
explicit CLI arguments still apply, and preferences still have to parse.

Relaunch stores **original arguments only** in `DPE_LAUNCH_ARGS`. Reusing resolved
`process.argv` would turn old settings into higher-priority overrides and make a
new preset appear ineffective. Ordinary restarts preserve recovery; the explicitly
labelled **Restart with saved flags** exits it. A stale OpenAsar settings page
cannot overwrite preferences saved by the separate editor.

Pinned upstream HTML is checksum-verified before a served-copy adaptation replaces
Focus with the editor entry point. Upstream bytes remain unchanged. The new native
window has a sandboxed preload, local assets/CSP and IPC scoped to its own main frame.
It uses native form controls, labelled descriptions, inline errors, unsaved-change
protection, draft reset and explicit Save/Restart actions.

## Explicit bypass and WIP controls

The subsequent experimental-controls update adds a prominent per-preset
**Force acceleration and bypass GPU checks** checkbox. It requests
`--ignore-gpu-blocklist`, `--enable-gpu-rasterization` and the features
`AcceleratedVideoDecoder,AcceleratedVideoDecodeLinuxGL,VaapiIgnoreDriverChecks,VaapiOnNvidiaGPUs`.
It does not claim a driver can accelerate an unsupported codec. Hardware encoding
remains independent; users can also select each existing bypass individually.

**Work-in-progress options** exposes the actual Graphite test override
(`--enable-skia-graphite --skia-graphite-dawn-backend=vulkan`) and
`--enable-gpu-memory-buffer-compositor-resources`. Each shows its exact flags.
The Graphite control uses a new `graphiteForce` key, so retired `graphite`
selections never activate the stronger override. Its known startup crash is
stated beside the checkbox. No sandbox or GPU crash-limit bypass is bundled.

These controls reuse the existing per-preset experiment list and schema. Turning
off the acceleration bundle removes only that selection; individual overrides
and WIP choices retain their own values. Reset/discard, manual feature precedence,
and the early flags-free recovery path continue to apply. The native editor test
covers selecting, saving, switching presets, reopening, reset/discard, accessibility
and recovery with these controls. Runtime observations are recorded in
[experimental-controls evidence](../evidence/experimental-acceleration.json).

## Validation and reproduction

The verified runtime executable SHA-256 was
`4223184105a4ee9f162bbfefabbe3a4f0eccc239400d38f88fa56390dd3d945d`.
Host: Arch Linux, niri `26.04-214-ge9804c1c`, AMD RX 7900 GRE, Mesa `26.2.4`,
libva `2.24.1`. No compilation, account login, messages, calls or physical camera use.

Run with fresh output directories and an already verified runtime:

```sh
uv run python scripts/check_launch_settings.py /path/to/runtime/electron \
  --output out/settings-check
uv run python scripts/check_launch_presets.py /path/to/runtime/electron \
  --output out/preset-check --full --codecs \
  --openh264 /path/to/libopenh264-2.5.1-linux64.7.so
bun run test
bun run lint
```

Without `--openh264`, the graphics fixture disables that optional dynamically
loaded codec. The matrix always probes independent decode/encode options, both
together and flags-free recovery. `--codecs` adds VP8/VP9/AV1/HEVC playback;
`--full` also probes each experimental option independently. Experimental failures
are recorded without treating experiments as required defaults. Both Wayland and
Xwayland must be available for the six-preset matrix. Reports retain resolved
arguments, GPU exits, decoder/encoder details and executable hashes.
Encoded bytes are decoded again with frame/timestamp/pixel checks. The ordinary
decoder path must pass; preferred hardware/software paths have separate results.
`check_graphics.py --strict-codecs` also fails on errors in those preference probes.
The current software H.264 decoder still fails despite advertising support.

Results are indexed in [preset evidence](../evidence/launch-presets.json):

- Balanced, Responsive and Efficiency passed on Wayland and Xwayland: hardware
  H.264 decode, hardware WebGL, and twelve software-encoded H.264/VP8/VP9/AV1 frames
  each, with no GPU exits.
- Flags-free recovery passed. This supersedes the older automatic-Ozone failure;
  it does not guarantee every compositor/driver's automatic path.
- Forced encoding reproduced a GPU crash and H.264 encoder error. Vulkan failed
  H.264 decoder initialization with `kUnsupportedConfig`. Both remain off.
- All other individual opt-ins in that initial catalog completed the synthetic probe. Graphite remained
  disabled, and an NVIDIA option passing on AMD says nothing about NVIDIA support.
  Completion is not a performance measurement or proof that a requested backend ran.
- The real Electron editor passed persistence, legacy-settings coexistence,
  validation/focus, per-preset drafts, separate codec controls, recovery repair and
  restart checks. Layout checks cover 320 CSS pixels, RTL and 200% zoom. The native
  fixture used only synthetic settings. It is not a full screen-reader audit.
- Launcher tests cover broken resolver/settings/files, CLI/env recovery, relaunch
  argument preservation, literal values, feature precedence and relocated packaging.

The separate forced-software H.264 playback limitation recorded in the earlier
investigation remains unresolved. Synthetic MP4/WebGL/WebCodecs checks do not prove
all uploaded videos, animated GIFs or live Discord streams work on every GPU.

### UI review scope

No actionable accessibility findings or layout findings remain in the inspected
editor flows. Native controls have accessible names/roles, validation associates
errors with fields and focuses the invalid control (WCAG 3.3.1/3.3.2), pending
Save retains focus, and keyboard Tab reaches the next control. Expanded options
reflow at 320 CSS pixels and 200% zoom without horizontal overflow (1.4.4/1.4.10),
including the RTL check. **Approve for this inspected scope.**

**Not verified:** a complete screen-reader speech walk, every operation performed
solely by keyboard, an independent automated WCAG audit, translated long-string
locales, and every intermediate window size. T3 preview snapshots timed out twice;
the native Electron fixture supplied the layout and accessibility-tree checks.
