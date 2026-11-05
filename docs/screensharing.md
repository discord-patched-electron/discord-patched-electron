# Screensharing

Open **User Settings → DPE Settings → Screensharing**. Choose resolution, FPS and
sound in Discord’s share dialog. See [screen-share audio](wayland-audio.md) for
application audio setup.

## Choose a capture method

Discord’s regular system picker works without extra setup. To try game or direct
capture, enable **Experimental capture methods** (off by default), then select
**Optimized** in Discord’s picker. Available sources depend on your desktop,
graphics API and driver. Use the system picker if your source is missing.
If a source disappears mid-share, capture stops instead of selecting another screen.

### Game capture

1. Start DPE once to register its game launcher.
2. Copy the **Game capture** command from DPE Settings into Steam’s launch options.
   It contains your launcher path followed by `-- %command%`.
3. Restart the game, then select it under **Optimized**.

For other launchers, replace `%command%` with the executable and arguments;
`%command%` is a Steam placeholder. Game capture needs no direct-screen permission.
Restart games after updating DPE to load the new hooks.

Optional prefixes for the copied command:

| Prefix                | Effect                                                          |
| --------------------- | --------------------------------------------------------------- |
| `env OBS_VKCAPTURE=1` | Vulkan only; skips the OpenGL hook.                             |
| `env OBS_COMPAT=1`    | Use installed `obs-gamecapture` hooks instead of bundled hooks. |

Combine them for stock Vulkan-only capture. Stock hooks share OBS’s receiver
endpoint, so OBS and DPE cannot receive from them simultaneously. These flags do
not establish anti-cheat compatibility. X11/XWayland game capture includes the
OS cursor; native Wayland games may need monitor capture to include it.

### Direct monitor capture

Supported sources use compositor capture protocols or DRM/KMS. For paths needing
system authorization, enable **Allow direct screen capture** and approve the
prompt. **Update permission** replaces an older rule.

Direct capture bypasses the desktop permission picker. Other apps running as
your user can invoke the authorized helper. Turning off direct capture or
experimental capture removes its rule after authorization; cancellation or a
removal failure restores the toggle. A status error explains obsolete permissions
or a restricted launch environment. Restart DPE outside that restriction if needed.

## Quality and codecs

Discord negotiates a codec supported by the sender and viewers:

| Codec        | Support                                                                               |
| ------------ | ------------------------------------------------------------------------------------- |
| H.264        | Native hardware encoding; managed OpenH264 for software reception.                    |
| HEVC / H.265 | Added AMD/Intel VAAPI encoding; native NVIDIA encoding. Requires a compatible viewer. |
| AV1 / AV1X   | Hardware-dependent encoding, with padding/cropping fixes for affected AMD hardware.   |
| VP8          | Software encoding; high resolutions and FPS can be CPU-limited.                       |
| VP9          | No encoder in the currently reviewed native module.                                   |

**Excluded codecs** applies to the next outgoing screenshare, leaving camera
calls and incoming decoding unchanged. Keep at least one usable encoder enabled.

**Match capture to the share frame rate** skips excess PipeWire frames before
processing them. DPE also improves encoder quality adaptation and frame pacing.
**Preserve bitrate in
smaller viewer windows** is off by default; enabling it can spend more bandwidth
on detailed sources. Network congestion and negotiated limits still apply.
Selecting a higher resolution cannot add detail absent from the source.

Affected AMD AV1 streams keep the source pixels and signal padding. Updated DPE
viewers crop it automatically; other viewers may show a border. DPE’s incoming
presentation fixes also require an updated DPE viewer.

## Troubleshoot a stream

Check **Current screenshare** for the capture method, active encoder, actual
resolution/FPS, bitrate and discard counters. Codec negotiation alone does not
prove encoding has started, especially without viewers. Incoming-video details
report **Hardware**, **Software** or **Not reported** decoding; a codec name alone does
not prove hardware acceleration.

Try the system picker to isolate capture problems. For poor quality, compare
requested and outgoing FPS, dimensions, bitrate allowance and discards. Hardware,
HDR, output transforms, games and receivers have different limits; identical
Windows performance is not guaranteed. Advanced bitrate overrides are covered in
[launcher settings](linux-launcher.md#screensharing); implementation and test
results are in the [screensharing review](agent/screenshare-review-20261105.md).
