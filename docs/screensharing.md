# Screensharing

DPE keeps Discord’s native screensharing and adds Linux capture, encoder and
viewer improvements. Open **User Settings → DPE Settings → Screensharing** to
configure them. Choose resolution, frame rate and audio in Discord’s share dialog.
For sound capture, see [application audio and desktop support](wayland-audio.md).

## Capture choices

The regular Discord/system picker remains available. **Experimental capture
methods** is off by default. Enabling it adds **Optimized** to Discord’s picker,
with previews of supported monitors and opted-in games.

- **Game capture:** Vulkan and OpenGL hooks capture an application directly.
  Start the game with the command shown in DPE Settings. Bundled hooks and
  installed OBS hooks are supported; games already running need a restart.
- **Direct Wayland capture:** uses supported compositor capture protocols.
  Availability depends on your desktop and its advertised capabilities.
- **Direct monitor capture:** uses DRM/KMS when supported and separately
  authorized. It captures the selected monitor without the portal picker.
- **PipeWire and X11:** Discord’s normal capture paths remain available when
  an optimized method cannot capture your source.

See [optimized capture setup](optimized-screensharing.md) for game launch options,
permissions and OBS compatibility. Direct capture bypasses the desktop permission
picker; applications running as your user may also invoke the authorized helper.
Keep it disabled unless you need it.

Supported paths keep frames on the GPU where possible. Capture pacing avoids
converting every frame of a high-refresh source for a lower-FPS share. Explicit
GPU fences remain authoritative; unsupported imports can use a copy path.
Resizes, FPS changes, selection cancellation and source loss are handled during
the share. A missing source ends capture rather than selecting another monitor.
Hardware cursors are included where the backend supports them. Native Wayland
games may need monitor capture to include the desktop cursor.

## Codecs and image quality

Discord still negotiates a codec supported by the sender and viewers. Hardware,
drivers and native-module capabilities determine which encoders can start.

| Codec        | DPE behavior                                                                                                                                                   |
| ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| H.264        | Native hardware encoding, including Mesa AMD quality/VBAQ tuning and corrected quality adaptation. Software reception uses Discord’s managed OpenH264 library. |
| HEVC / H.265 | Added VAAPI hardware encoding for compatible AMD and Intel devices; NVIDIA retains its native encoder. Viewers need compatible HEVC support.                   |
| AV1 / AV1X   | Native hardware encoding where available, plus a native-size alignment workaround for affected Mesa AMD VCN4 hardware.                                         |
| VP8          | Native libvpx software encoding remains available. High-resolution/high-FPS performance depends on CPU capacity.                                               |
| VP9          | Not advertised by the currently reviewed Discord native encoder. Installing another system codec does not add that implementation.                             |

**Excluded codecs** affects your next screenshare’s encoder selection. It does
not disable incoming decoding or change camera-call codecs. If all available
encoders are excluded, allow another codec before sharing.

The encoder changes preserve negotiated rate control and congestion adaptation.
They address incorrect quality-scaler decisions and bitrate-envelope changes
that could reduce motion quality. HEVC/AV1 VAAPI encoding uses no B-frames and
keeps its submission queue short. Driver-specific settings are scoped to the
hardware they apply to; AMD alignment restrictions do not apply to NVIDIA or Intel.

### AMD AV1 dimensions

Affected hardware needs different encoded dimensions from the visible image.
DPE preserves the original pixels instead of shrinking or stretching them.
Where supported, a 1920 × 1080 source is encoded as 1920 × 1082. Other dimensions
can require a larger aligned frame with centred padding. Alignment is calculated
from the source and backend, rather than assuming every source is 1080p.

Updated DPE viewers automatically remove signalled padding in both supported
hardware decoding and native software AV1 decoding. Cropping changes the visible
frame without copying its pixels. Genuine unpadded content is left intact.
The sender carries crop information in proposed AV1 metadata, so ordinary viewers
may still show a small border. This is not universal receiver support.

## Bitrate and smoothness

There is no single guaranteed Discord bitrate. Selected quality, source size,
viewer requests, codec negotiation and available network bandwidth all matter.
A simple or static picture can use less than its allowance without losing detail.
Choosing 1440p for a 1080p source does not create additional source detail.

**Preserve bitrate in smaller viewer windows** is **off by default**. Discord’s
normal viewer-size policy therefore remains active. Enable it if small viewer
tiles leave a detailed share with too little bitrate allowance. DPE then budgets
headroom from the source size and selected quality, updating when the source
resizes. This can use more bandwidth; it does not force a constant outgoing rate
or prevent congestion adaptation.

An advanced test override, `DPE_SCREENSHARE_MAX_BITRATE`, can request a higher
ceiling in bits per second. Accepted values are above 9,000,000 and at most
32,000,000. It is disabled by default and does not guarantee that Discord’s
negotiation or network will permit that rate. Raising it is not a general fix
for poor compression or frame drops.

Sender packet pacing reduces queues during scene changes. Updated DPE viewers
also use corrected frame timestamps and Chromium’s remote-video presentation
scheduling. Sender improvements benefit compatible ordinary viewers; DPE’s
presentation and automatic crop changes require an updated DPE viewer.

## Check a stream

**Current screenshare** reports the active capture method, codec, encoder,
resolution, FPS and bitrate when Discord exposes them. Its details include
quality adaptation, discard counters and the configured bitrate allowance.
A negotiated codec by itself does not prove encoding has started, particularly
when nobody is watching.

The incoming-video section reports the decoder and **Hardware**, **Software** or
**Unknown** status for streams you are watching. Unknown means the native decoder
did not provide a trustworthy acceleration flag. A codec name alone does not
establish hardware decoding.

For a poor stream, compare requested and outgoing FPS, actual dimensions, bitrate
allowance and discard counters. Try the regular system picker to isolate a
capture-method issue. Hardware codec support varies by driver; VP8 may be
CPU-limited, and HEVC requires a compatible receiver. HDR, transformed outputs
and individual games can have backend-specific limitations.

Testing covers generated native encode/decode round trips and selected live
scenarios. It does not establish identical performance to Windows on every GPU,
desktop, game or network. Technical changes and validation are indexed in the
[screensharing review](agent/screenshare-review-20261105.md).
