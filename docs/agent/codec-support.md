# Codec playback verification

Checked on 2026-10-27 using the verified Electron 46.0.0-alpha.1 / Chromium
158.0.8114.0 runtime, which has the same executable hash as the portable test app.
The local machine has an AMD RX 7900 GRE with Mesa 26.2.4. The reported HEVC problem
in the supplied discussion was on someone else's NVIDIA GPU; that hardware and
driver were not available for testing. [Recorded evidence](../evidence/codec-support.json).

## Observed decoding

| Codec                 | Forced software decoding                       | Default decoding on this AMD GPU                                                |
| --------------------- | ---------------------------------------------- | ------------------------------------------------------------------------------- |
| H.264, 8-bit          | Passed: FFmpeg's external OpenH264 decoder     | Passed: VA-API                                                                  |
| VP8, 8-bit            | Passed: libvpx                                 | Passed through libvpx; this GPU's VA-API driver does not advertise VP8 decoding |
| VP9, 8-bit and 10-bit | Passed: libvpx                                 | Passed: VA-API                                                                  |
| AV1, 8-bit and 10-bit | Passed: dav1d                                  | Passed: VA-API                                                                  |
| HEVC Main / Main 10   | Unsupported: no software decoder in this build | Passed: VA-API                                                                  |

All 25 cases matched their expected outcomes: 22 successful playback cases and
three unsupported software-HEVC cases. The latter are failures to play, not
software-decoder passes. HEVC and AV1 10-bit also passed on Xwayland. All successful
cases completed a synthetic 1280×720, 30 fps, 4:2:0 clip and sought back after
end-of-stream. The renderer sandbox remained enabled (`Seccomp: 2`,
`NoNewPrivs: 1`), and no GPU crashes occurred in this matrix.

The Media debugger recorded the actual decoder and platform-decoder status for
the clip. `ffprobe` confirmed its encoded profile and pixel format. These results
do not come from capability queries or the GPU feature-status list alone. The two
full H.264 cases also retained strict H.264/VP8/VP9/AV1 WebCodecs round trips with
frame-count, timestamp, dimension and pixel checks.

The assembled build has `ENABLE_DAV1D_DECODER=1`, `ENABLE_LIBVPX=1`,
`ENABLE_PLATFORM_HEVC=1`, `ENABLE_HEVC_PARSER_AND_HW_DECODER=1`, and `USE_VAAPI=1`.
Its FFmpeg configuration has `CONFIG_HEVC_DECODER=0` and
`CONFIG_LIBOPENH264_DECODER=1`. Direct lookup in the verified `libffmpeg.so` also
found `libopenh264` and no `hevc` decoder. dav1d and libvpx are separate Chromium
decoders, so their absence from FFmpeg's decoder registry does not remove software
AV1/VP8/VP9 support. The [OpenH264 preload fix](software-codecs.md) remains necessary
for software H.264 in the sandbox.

## Flags from the discussion

| Feature                                 | Finding for this pinned Linux runtime                                                                                                                                                                                                                                                       |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `VaapiOnNvidiaGPUs`                     | Disabled by default. The [VA-API wrapper][vaapi-wrapper] skips `nvidia-drm` devices unless enabled. This is the likely relevant difference for the reported NVIDIA machine. It still needs a working VA-API driver and support for the video's codec/profile.                               |
| `PlatformHEVCDecoderSupport`            | [Enabled by default][media-switches]. The [desktop Linux capability path][supported-types] uses the available decoder-profile cache rather than this feature check. HEVC playback passed with the flag absent, explicitly enabled, and explicitly disabled. It cannot add software HEVC.    |
| `AcceleratedVideoEncoder`               | [Disabled by default][media-switches] and controls encoding, not playback. Enabling it is unnecessary to watch HEVC. Earlier [encoding tests](flags-file.md#crash-labels-and-further-flag-research) reproduced GPU crashes on this AMD machine when the hardware encoder was actually used. |
| `AcceleratedVideoDecodeLinuxZeroCopyGL` | [Enabled by default][linux-decoder]. Chooses eligible NV12/P010 buffer-import paths; it does not enable the HEVC codec. HEVC Main 10 playback also passed with it disabled.                                                                                                                 |

The exact four-feature bundle from the discussion played HEVC Main 10 here, as
did the default configuration. That comparison exercised playback without starting
encoders, so it does not qualify hardware encoding. No extra default flags were
added. Existing automatic VA-API decoding already works on this AMD setup.

For the NVIDIA user, the targeted flag to investigate is
`--enable-features=VaapiOnNvidiaGPUs`, rather than enabling encoding as part of a
playback workaround. The Flags tab now exposes this as **Allow VA-API on NVIDIA
GPUs**, off by default, with a link to its required driver. The option alone does
not enable encoding or bypass driver checks. The [NVIDIA VA-API driver's current README][nvidia-driver]
describes a Chromium-compatible buffer export path and use of
`LIBVA_DRIVER_NAME=nvidia`; installed driver versions and GPU capabilities still
need verification. A flag alone cannot provide a missing or incompatible driver.
If NVIDIA hardware decoding is unavailable, this runtime can fall back to software
for H.264/VP8/VP9/AV1, but **HEVC cannot fall back to software**. A complete NVIDIA
diagnosis needs that machine's GPU, driver versions, `vainfo` and failing clip.

[media-switches]: https://github.com/chromium/chromium/blob/158.0.8114.0/media/base/media_switches.cc
[supported-types]: https://github.com/chromium/chromium/blob/158.0.8114.0/media/base/supported_types.cc
[linux-decoder]: https://github.com/chromium/chromium/blob/158.0.8114.0/media/mojo/services/gpu_mojo_media_client_linux.cc
[vaapi-wrapper]: https://github.com/chromium/chromium/blob/158.0.8114.0/media/gpu/vaapi/vaapi_wrapper.cc
[nvidia-driver]: https://github.com/elFarto/nvidia-vaapi-driver#chrome

## Software HEVC and licensing review

This update keeps HEVC hardware-only. No decoder library was added, downloaded,
replaced or bundled, and Electron was not rebuilt. The user authorized only a
local build of this candidate if needed; no CI compiler was started and retained
build objects remain untouched. This is a conditional implementation decision:
the requested assurance against licensing violations could not be established
for a new software HEVC implementation. It is not a finding that such an
implementation is inherently unlawful.

The existing verified runtime reports `LGPL version 2.1 or later` from
`avcodec_license()`. Its matching FFmpeg configuration has `CONFIG_GPL=0`,
`CONFIG_NONFREE=0`, `CONFIG_VERSION3=0` and `CONFIG_HEVC_DECODER=0`. The runtime's
`LICENSES.chromium.html` contains the FFmpeg, dav1d and libvpx notices. Chromium's
source metadata identifies dav1d as BSD-2-Clause and libvpx as BSD-3-Clause with
the WebM patent licence. Those notices and the runtime are retained unchanged;
these checks do not constitute a patent-clearance opinion.

Two system-library approaches were examined:

- **System FFmpeg:** Chromium provides `build/linux/unbundle/ffmpeg.gn`, but this
  pinned runtime's `FFmpegVideoDecoder::IsCodecSupported` accepts only H.264 and
  its thread-selection path rejects HEVC. Linux HEVC capability reporting also
  uses the available platform-decoder profiles. Replacing `libffmpeg.so` alone
  therefore cannot provide software HEVC. A system-FFmpeg build would additionally
  need API/ABI compatibility work and preservation of Discord's custom OpenH264
  symbols. This machine's FFmpeg 9.0.2 package is GPL-3.0-only; it is not a
  licence-equivalent replacement for the shipped LGPL build. FFmpeg's
  [licensing guidance](https://ffmpeg.org/legal.html) explains the distinction
  between LGPL builds and builds with optional GPL components.
- **System libde265:** version 1.1.3 is installed here. Its public C API and
  Main/Main 10 support make a separate decoder adapter technically plausible.
  It is [LGPL-3.0-or-later](https://github.com/strukturag/libde265/blob/v1.1.3/libde265/de265.h).
  [LGPLv3 section 4](https://github.com/strukturag/libde265/blob/v1.1.3/COPYING)
  provides a shared-library route with notices, licence copies, replacement and
  debugging rights, and applicable installation-information obligations. An
  adapter would need to load the library before sandbox lockdown, advertise
  support only when it can initialize, handle frame layout/timestamps and
  reset/drain correctly, and pass software Main/Main 10 playback and seek tests.
  Merely installing or preloading libde265 does not connect it to Chromium.

Using a system library can avoid shipping its binary, but does not by itself
establish clearance for third-party codec patents. The LGPL patent grant concerns
contributors' applicable claims, not arbitrary patent owners. The current
[HEVC Advance FAQ](https://accessadvance.com/faq/) says free downloaded HEVC
software generally needs a licence under its program, with case-specific
exceptions; its statement is the licensor's position, not a determination about
this project. [VCL Advance](https://www.via-la.com/licensing-programs/hevc-vvc/)
also describes a separate portfolio. No applicable licence, exemption or legal
assessment for DPE was supplied or established here. Neither shared-library
loading, open-source availability nor a no-cost download proves that issue
resolved. Establish the relevant distribution/use rights before integrating or
redistributing a software HEVC decoder. Existing hardware playback evidence is
not a legal clearance for every GPU or distribution either.

The research and runtime-notice checks are recorded in
`out/nvidia-info-ui/hevc-licensing.json` and `runtime-licenses.json`.

## Repeating the checks

`scripts/check_graphics.py` now accepts `--playback-only` to test decoding without
initializing any encoder, and `--pixel-format yuv420p10le` for 10-bit clips. Normal
round-trip tests remain enabled by default. `clip-probe.json` records the actual
encoded stream. Seeking now observes the requested frame while paused before
resuming; the previous fixture could mistake a late compositor callback for a
failed seek. The initial raced HEVC report is retained separately from the matrix.

For example, on a real Wayland session with the verified runtime:

```sh
uv run python scripts/check_graphics.py /path/to/electron \
  --output out/hevc-main10-check \
  --video-codec hevc --pixel-format yuv420p10le --playback-only \
  --flag=--ozone-platform=wayland
```

Use a fresh output directory for each run and a short disk-backed `TMPDIR` if
`/tmp` has a low quota. Add `--flag=--disable-accelerated-video-decode` to test
software fallback. For software H.264, also pass `--launcher-codecs`,
`--flag=--enable-libopenh264` and `--flag=--openh264-library-path=/trusted/library.so`.
The full matrix's flags, outcomes and report hashes are in the evidence JSON;
local raw reports are under `out/codec-audit/verified/`.

No runtime compilation, account login, Discord call, physical camera capture or
desktop recording was performed. These short synthetic checks do not establish
HDR display accuracy, all chroma/profile combinations, sustained performance or
behavior on other GPUs. No release or installed app was changed.
