# Screensharing

Open **User Settings → DPE Settings → Screensharing** for capture and quality
settings. Choose resolution, FPS and sound in Discord’s share dialog. See
[screen-share audio](wayland-audio.md) for application audio setup.

## Choose a capture method

Use Discord’s **system picker** for sharing without extra setup. To try game or
direct monitor capture, enable **Experimental capture methods** (off by default),
then open **Optimized** in Discord’s share picker.

### Why try Optimized capture?

Bundled game capture takes frames directly from a Vulkan or OpenGL game. Direct
monitor capture uses a supported compositor protocol or DRM/KMS to capture a
display. These methods can reduce the work your desktop does to capture a stream.

On supported DPE versions and drivers, bundled game capture and KMS pass GPU
buffers directly to Discord, skipping the PipeWire video stream. With compatible
VAAPI hardware encoding, frames stay on the GPU through conversion and encoding,
avoiding full-frame copies through CPU memory. Fewer capture stages can reduce
delay and leave more resources for the game. Game capture can also avoid the
compositor’s screen-capture work.

Benefits depend on your GPU, driver and workload; Optimized is not always faster.
Compositor-protocol capture and stock OBS hooks use their existing video paths.
PipeWire still handles application audio. If your source is missing or capture
fails, use the system picker. Losing a source stops the share rather than
switching to another screen.

### Set up game capture

1. Start DPE once to register its game launcher.
2. Copy the **Game capture** command from DPE Settings into Steam’s launch options.
   It includes your launcher path followed by `-- %command%`.
3. Restart the game, then select it under **Optimized**.

For other launchers, replace `%command%` with the game’s executable and arguments.
Restart games after updating DPE to load the new hooks. Game capture needs no
direct-screen permission. Vulkan/OpenGL game capture currently omits the cursor;
anti-cheat compatibility is not guaranteed.

Optional prefixes for the copied command:

- `env OBS_VKCAPTURE=1`: capture Vulkan only; skip the OpenGL hook.
- `env OBS_COMPAT=1`: use installed `obs-gamecapture` hooks. OBS and DPE cannot
  receive from these hooks simultaneously. Combine both flags for stock
  Vulkan-only capture.

### Set up direct monitor capture

For sources requiring system authorization, enable **Allow direct screen capture**
and approve the prompt. AppImage installations use the same switch.

Direct capture bypasses the desktop permission picker. Permission is limited to
the authorized Discord executable and its capture workers. Initial approval also
allows verified helper updates without further authentication; modified or
unsigned updates are rejected. If permission needs an update, the switch turns
off; enable it again to approve the current build.

Turning off direct capture or experimental capture removes the system rule after
authorization. Cancelling or failing removal restores the toggle. Package removal
also cleans up permissions; see [uninstalling](install.md#uninstall) for portable
cleanup.

## Adjust quality

DPE improves compression, color detail and frame pacing, and fixes capture resize
and restart handling. Codec support determines which video compression Discord
can use; Discord chooses a codec supported by both the sender and viewers.

- **H.264:** fixes quality and bitrate decisions, with better compression settings
  on affected AMD hardware.
- **HEVC / H.265:** adds hardware encoding for supported AMD/Intel devices and
  keeps NVIDIA’s existing path. Viewers need HEVC support.
- **AV1 / AV1X:** enables supported AV1 encoding. Affected AMD streams preserve
  the source image with padding; updated DPE viewers crop it, while other viewers
  may show a border.
- **VP8:** uses CPU encoding and can struggle at high resolutions or FPS.

A GPU’s codec support alone does not guarantee hardware encoding: working drivers
and Discord support are also required. VP9 has no encoder in the supported Discord
module, so it is absent from **Excluded codecs**.

Use these settings for specific problems:

- **Match capture to the share frame rate:** skips excess PipeWire frames before
  processing, reducing unnecessary work on high-refresh sources.
- **Preserve bitrate in smaller viewer windows:** keeps more video data for
  detailed sources when viewers use small tiles. Off by default; it can increase
  bandwidth use. Network congestion and negotiated limits still apply.
- **Excluded codecs:** disables selected codecs for your next outgoing screenshare.
  Keep at least one usable encoder enabled. Camera calls and incoming video are
  unaffected.

Higher resolution cannot restore detail missing from the source. DPE’s viewer
timing and cropping improvements require an updated DPE viewer.

## Troubleshoot a stream

Check **Current screenshare** for the capture method, GPU handoff, active encoder,
actual resolution/FPS and bitrate. **Encoding details** shows quality limits and
discard counters. Compare these with your requested settings to find where quality
or frame rate drops. A negotiated codec does not prove encoding has started;
without viewers, the encoder may remain idle.

Try the system picker if Optimized capture fails. For permission errors, follow
the settings error; a restricted launch environment may require restarting DPE
outside that restriction. **Incoming video** reports hardware/software decoding;
the codec name alone does not prove hardware acceleration.

For implementation details and measured limits, see the
[capture comparison](agent/benchmarks.md#native12-resource-comparison) and
[screensharing review](agent/capture.md#qualification-and-remaining-work).
