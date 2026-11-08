# Receiver presentation clock and AMD AV1 padding, 4 November 2026

Current implementation and evidence supersede the AV1 downsampling workaround in
[the earlier codec note](hevc-av1-chroma-20261104.md). Previous Windows, NVIDIA
and Intel audits remain useful context, not current hardware measurements.

## Receiver clock

Live testing used the existing, logged-in viewer profile and a muted test call.
Only technical timing and codec data were retained; no desktop/call recordings
or account credentials were collected. Viewer restarts required reopening the
existing call and stream. No chat messages were sent.

Discord's reviewed `DirectOutputStream::FinishOnFrame` reads a 32-bit value at
`webrtc::VideoFrame + 0x10` into `DiscordFrame.timestamp_us`. This is the 90 kHz
RTP timestamp, not microseconds. The signed 64-bit microsecond field is at
`+0x20`; the module's `VideoFrame::render_time_ms()` independently confirms that
field by dividing it by 1000. The equivalent reviewed load is at file offset
`0x44d0b0` in the retained module and `0x44a9c0` in the current pinned module.

`scripts/patch_presentation_clock.py` changes only two instruction bytes, uses
whole-file input/output hashes for both reviewed modules, rejects unknown input,
and replaces the file atomically. Module preparation, update compatibility,
package sources, build fingerprints and native scope aliases include it.
Idempotent preparation of the actual already-patched module was verified.

The software output branch already interprets this field as microseconds.
Hardware decoding needs a second correction: Electron previously used the
underlying decoder frame directly, whose timestamp still carries the RTP clock.
The project-owned `presentation-clock.patch` wraps that frame's existing storage
and sets the wrapper's timestamp from `DiscordFrame.timestamp_us`. It retains
format, visible/natural geometry, metadata and pixel ownership. It does not
perform a GPU blit, CPU readback or mutate the decoder's shared original frame.
The pinned upstream integration patch is untouched. The retained Electron build
compiled one source object and relinked successfully.

In the real AV1X/Electron-decoder viewer, the old clock advances approximately
1.53 ms for a 16.7 ms wall interval and reports about 667 FPS for a 60 FPS share.
The corrected viewer advances approximately 17 ms and reports approximately
60 FPS. The sender's self-preview delivered 1200 callbacks in 20 seconds with no
45 ms gaps, but had zero media-time deltas; it is a different output path and
cannot establish receiver timing correctness.

Numeric timing artifacts and test launchers are under
`out/receiver-investigation-20261104/`. Callback timing is a presentation proxy,
not a high-speed camera measurement of panel scanout. Window visibility,
viewport size, refresh rate, network jitter and source changes affect results.
A staging error replaced a loaded helper in place and invalidated one test run
with renderer/GPU crashes; subsequent staging uses atomic replacement and never
changes live executable mappings. This must not be counted as a clock-fix result.

## AMD AV1 geometry

AMD's [VCN4 investigation](https://github.com/GPUOpen-LibrariesAndSDKs/AMF/issues/423)
documents a hardware encoding alignment limitation, also on Windows. Merely
setting a render-size hint is not a portable crop: the
[AV1 specification](https://aomediacodec.github.io/av1-spec/) says render size does
not affect decoding. The 1082-line workaround still exposes extra lines, while
1088 matches the existing 16-line hardware requirement cleanly. No new user
rounding setting or card blacklist was added.

Only the verified VCN4 AV1 workaround selects the resizable private backend.
It rounds coded width up to a multiple of 64 and height up to a multiple of 16.
The original source is copied at 1:1 sampling, centred at even NV12 offsets;
opposite margins can differ by two luma pixels to preserve the chroma grid.
1920×1080 becomes 1920×1088 with four black lines above and below. Widths are
handled generically too. Aligned input requires no padding/VPP pass. The
existing GPU VPP pass performs padding for unaligned input. Each pooled output
surface is initialized once with neutral NV12 black; steady-state frames add no
extra pass, CPU readback or black upload. A VCN4 share starting at an aligned size also uses
this backend when available, so a later unaligned window resize retains AV1.

Other AMD generations, Intel AV1 and NVIDIA NVENC keep their native paths.
HEVC keeps the requested even visible dimensions. The GPU fixture checks both
HEVC and AV1 at 256×256, 640×360, 1280×720, 1920×1080 and 1918×1078. It encodes
one-pixel luma stripes and all four source edges, decodes them, and compares the
original coordinates plus neutral black margins. All ten cases passed on the
available AMD GPU with less than one luma level mean error, no pixels above the
20-level detail-error threshold, and neutral padding chroma (maximum one-level
error in the tested cases). This establishes no resampling in these cases;
lossy encoding still means decoded values are not mathematically pixel-identical.

Stock receivers display the padded coded picture. There is no validated,
portable Discord RTP crop signal here. At a viewport that scales the whole
1088-line frame to 1080 lines, normal player scaling can still occur. This
change preserves source pixels through the encoder input; it cannot promise
pixel-exact panel output at every receiver viewport.

Reproduce generated-only GPU checks with:

```sh
uv run python scripts/build_camera_support.py --output out/my-codec-check
uv run python scripts/check_vaapi_padding.py \
  --backend out/my-codec-check/hevc/libdpe-hevc.so \
  --render-node /dev/dri/renderD128 --output out/my-padding-check
uv run python scripts/check_vaapi_backend.py \
  --backend out/my-codec-check/hevc/libdpe-hevc.so \
  --render-node /dev/dri/renderD128 --output out/my-lifecycle-check
```

## Encoder/decoder review

The [Windows audit](windows-encoder-parity-20261104.md) recovered actual AMF
requests rather than assuming recorder presets equal Discord defaults. Windows
requests no B-frames, enables VBAQ, and uses latency-dependent RC/HRD/analysis.
Its dynamic VBV arguments do not establish a universal fixed bitrate or queue
size. Native Windows requests are not proof of accepted hardware settings or
measured end-to-end superiority.

The private VAAPI path uses no B-frames, async depth one, a low-delay flag, a
long GOP with explicit keyframe recovery and a one-second bitrate reservoir.
That reservoir is an encoder bit budget, not a one-second frame queue. Mesa's
packed quality setting is restricted to the Mesa AMD driver; it is not a
portable Intel/NVIDIA quality scale. Current allocations and negotiated maximum
bitrate remain separate; the earlier experiment clamping peak to target was
removed. No filler traffic or blanket bitrate increase was introduced.

[NVIDIA's programming guide](https://docs.nvidia.com/video-technologies/video-codec-sdk/13.1/nvenc-video-encoder-api-prog-guide/index.html)
distinguishes low/ultra-low latency from quality modes, B-frame reordering,
lookahead and platform-specific completion handling. Native NVENC is retained;
forcing multipass, lookahead or a slower preset without NVIDIA measurements
would not be evidence-based. See the [NVENC audit](screenshare-nvidia-20261102.md).
[Intel's feature table](https://github.com/intel/media-driver/blob/master/docs/media_features.md)
is generation-specific; AV1 decoding does not imply encoding, and bitrate
control can depend on HuC/entrypoint capabilities. See the
[Intel audit](screenshare-intel-20261102.md). Neither physical GPU was available.

The receive-clock correction applies to all codecs reaching this reviewed
direct-output ABI, independently of encoder vendor. Actual hardware decode
support still depends on Chromium, driver, codec/profile and the selected GPU;
no new hardware support is inferred from NVENC or VAAPI encoding capability.

## Reload-safe optimized capture

The live investigation also reproduced optimized shares falling back to the
portal after a renderer document reload. `/proc/self/mem` read-only diagnostics
confirmed that all four native picker GOT entries still pointed at the helper,
but `closing` was true and the TSFN dispatcher was null. The old environment's
cleanup had destroyed the JS callback; the new environment's install returned
early because the native hook was already installed. The unchanged hook then
intentionally fell through to the portal because it had no dispatcher.

`capture-picker.c` now rebinds the callback when this exact already-installed
module is initialized after cleanup. Cleanup is tied to its own dispatcher,
so an old environment cannot tear down a replacement callback. Abandoned
waiting requests lose their retired callback references; selected FD ownership
and active captures retain their existing rules. The generated C regression
checks cleanup, rebinding, request delivery, stale cleanup and repeat cleanup.

The logged-in sender was deliberately navigated through a full document reload,
then shared the optimized DP-2 monitor. Native diagnostics showed the replacement
dispatcher active, KMS selected with no portal window, 1920×1080 source geometry,
60 FPS negotiated/applied, DMA-BUF frames and no capture-return errors. This also
explains why some earlier live runs used the portal instead of the intended KMS
path; comparisons must retain that capture-backend distinction.

## Source budget versus viewer tile size

The live paired sender/receiver trace exposed a second bitrate reduction. The
sender was encoding 1920×1080 at a 2.75 Mbps maximum, despite approximately 8 Mbps
of connection bandwidth. Read-only diagnostics of the project-owned private
encoder confirmed the real target/maximum, rather than interpreting the
misleading `targetMediaBitrate` native JSON field as an allocation.

The reviewed upstream `getGoliveQuality` retains `goliveMaxQuality.encode` and
`capture` when reducing bitrate for viewer pixel wants. The newer source-aware
DPE heuristic had also taken the minimum with the viewer tile pixels, reducing
the ceiling even though the actual encoder still processed the full source.
In a generated 240 FPS scene-change workload, the 20-second trace recorded 124
media-optimization drops, no encoder/encoder-queue drops, a 344 ms maximum pacer
delay and fast decoding. These distinguish that bottleneck from slow decoding;
they do not establish that every freeze has the same cause.

The budget now uses the source raster bounded by selected encode quality,
without treating tile pixels as encoder pixels. It remains bounded by negotiated
limits and the native congestion controller. The policy match explicitly checks
that upstream retains those encode/capture objects. No bitrate limit is raised.

Source-size changes now call the reviewed `updateVideoQuality` path, which
refreshes both constraints and native `streamParameters`. Reapplying unchanged
`setDesktopEncodingOptions` is a native no-op; updating only global constraints
also leaves a smaller per-stream maximum cached. The regression test checks the
native stream maximum changing with source size, retention of FPS/selection,
and a small tile retaining the full-size source budget.

## Final checks and remaining gaps

The full generated native Discord loopback passed for AV1 with 1920×1088 output,
all source-edge checks, hardware/Electron decoding, 60 FPS output from a 240 FPS
synthetic source, DMA-BUF input, synchronization, picker cancellation and active/
idle transport checks. It checked 355 padded frames with no failures; maximum
interior padding RGB was 13. HEVC at 1920×1080 also passed the full set of checks.
H.264 passed both the software/FFmpeg and Electron-requested decode paths when
provided the normal OpenH264 dependency. Omitting that dependency failed decode;
normal launchers acquire/preload it. An implementation name alone is not an
independent measurement of the physical decoder used.

The initial luma-only padding test missed a real chroma defect: new surfaces had
near-zero U/V outside the copied rectangle, producing green borders. The
[libva background-colour contract](https://intel.github.io/libva/structVAProcPipelineParameterBuffer.html)
requests black outside the output rectangle, but Mesa's
[compositor VPP path](https://gitlab.freedesktop.org/mesa/mesa/-/blob/main/src/gallium/auxiliary/vl/vl_compositor_proc.c)
does not implement that background fill in the observed path. The backend now
uploads a neutral NV12 black image once per pooled output surface, then reuses
those initialized surfaces. The pool and initialization cache are retired on
resize. Tests now verify both luma and chroma and reconstruct RGB in the actual
native receiver; they do not accept arbitrary oversized pictures or unknown
hardware as valid padding.

Actual account testing used 60 FPS KMS capture on a 240 Hz output, and generated
content running at 240 FPS. It also exercised a temporary 30 FPS/720p selection
and restoration to the original 60 FPS/1440p maximum; receiver wants could adapt
the actual raster below that maximum. One 30 FPS timing sample was invalidated
by offscreen/background throttling (approximately 1 Hz rAF). Other 30/60 FPS
motion samples still had gaps. Window position, viewport and rAF timing must be
recorded when interpreting these artifacts.

The clock correction is verified, and steady runs delivered roughly 1200 frames
in 20 seconds without gaps over 25 ms. This does **not** establish universally
smooth motion. At a full-rate allocation, repeated generated scene changes still
produced media/pacer gaps while decoding remained fast. A 200 ms reservoir with
a two-frame low-FPS floor passed image/lifecycle checks but did not consistently
bound pacer delay: a live run still exceeded 500 ms. That experiment was reverted;
the existing reservoir remains. Increasing available allocation alone is not a
judder fix. Paired traces and experiment summaries are retained in the output
directory, including unsuccessful and non-comparable runs.

No Windows machine, NVIDIA GPU or Intel GPU was physically tested. Static vendor
isolation and ABI tests cover those branches; this is not a claim of measured
Windows parity. The main unresolved performance work is scene-change burst
control and pacing, with matching source geometry, network state and actual
encoder allocation in the comparison. There is also no portable stock-receiver
crop for the AV1 padding.
