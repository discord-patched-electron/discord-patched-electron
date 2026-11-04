# Native GPU capture dimensions and compositor synchronization, 2 November 2026

## Verified dimension defect

Discord's reviewed native Linux GPU capturer rounds both visible dimensions to
the nearest multiple of 64. This happens in
`capture_linux::resolution::calculate_pool_resolution`, before VA-API encoding.
It is not evidence that AMD requires a visible 1088-pixel picture for 1080p.

With the private, linear BGRA DMA-BUF producer on the RX 7900 GRE, requesting
1920×1080 produced native 1920×1088. That exceeds the configured 2,073,600-pixel
WebRTC limit. WebRTC then requests 1680×952, which the native frame does not have
as a GPU rendition. `WumpusFrameBuffer::CropAndScale` logs the missing rendition
and calls WebRTC's CPU `VideoFrameBuffer::CropAndScale` for each affected frame.
The same synthetic source via memfd remains 1920×1080.

This changes aspect ratio, reduces resolution and adds CPU conversion work.
Raising the requested output to 1920×1088 hides the missing rendition, but changes
the user's stream dimensions. That earlier diagnostic is not the shipped fix.

The final arithmetic is `(dimension + 32) & ~63`, not a ceiling operation. It
also changes 1280×720 to 1280×704 and 640×360 to 640×384. The common return follows
the native crop/aspect-fit calculation and the existing 4096 maximum dimension.

## Narrow correction and review

`scripts/patch_capture_dimensions.py` changes the common return to
`(dimension + 1) & ~1`, preserving even visible dimensions and adjusting odd
window dimensions by at most one pixel for 4:2:0. It does not change the crop,
aspect-fit limit, shader, allocation API, structure layout, call ABI or metadata.

Only four immediate bytes change in a 12-byte instruction sequence:

```text
before: 83 c0 20 83 e0 c0 83 c1 20 83 e1 c0
after:  83 c0 01 83 e0 fe 83 c1 01 83 e1 fe
```

The script accepts only two full-file hash pairs, after the existing HRD fix:

| Reviewed build          | File offset | HRD-corrected input SHA-256                                        | Dimension-corrected SHA-256                                        |
| ----------------------- | ----------- | ------------------------------------------------------------------ | ------------------------------------------------------------------ |
| Earlier reviewed module | `0x157408e` | `0eecfa64b0120ca85c6454a39cf011f1f52d5661b33868ada0822580e644490e` | `23dae94b2adcc295faa473fd5869b17cbc19154eec50d6cdf5970ef4f921b0c5` |
| Pinned Canary 1.0.2357  | `0x15719ae` | `62812b1287d5ea13fd762e91c18a20532734080cd3b30f0059b538281c0c4f64` | `72096efc87a407b975e9913477d387e2de9a1f1028575c1f97a22365bacb007c` |

Unknown or modified binaries fail closed. Prepared files are atomically replaced
after full output validation; hardlinked input files and running mappings remain
unchanged. Reapplying the correction is idempotent.

Preparation applies HRD first and dimensions second. The HRD stage recognizes
the two exact final hashes as already corrected, so preparing a complete module
again preserves its dimension correction. A dimensions-only partial edit is
unsupported. The private staging helper validates both stages before making an
output directory and records each stage's input/output hashes separately.
Updater and package source lists include the new script; the updater compatibility
key includes its contents so old prepared generations are rebuilt. Capture scope
recognizes the two exact new hashes, retaining the same callback/ABI checks.

Static review established why visible 64-pixel alignment is unnecessary here:

- `VulkanNv12LinearDescriptor::new` separately rounds row pitch up to 256 bytes
  and derives NV12 buffer length from that pitch and the logical height.
- `VulkanScaler::scale` already rounds compute dispatch up to 16×16 workgroups.
- The actual embedded SPIR-V checks both coordinates against `dstSize` and returns
  before any output write outside the image. Its included GLSL confirms the same
  behavior. Chroma output requires even dimensions, which the correction retains.
- The two reviewed `calculate_pool_resolution` functions match after normalizing
  relocation addresses. Both embedded shader blobs have SHA-256
  `aadde83936f48237cd08ba2bb781ffc576c3245a227edf50dda8fdd23f36284a`.

Generated review evidence is under `out/capture-implementation-20261102/`:
`native-pool-resolution.txt`, `native-nv12-allocation.txt`,
`native-vulkan-scale.txt`, `native-vulkan-scaler-new.txt`,
`native-vulkan-scaler.spv`, `native-vulkan-scaler.spvasm`, and
`dimensions-static-review.json`. These contain local binary analysis and are
not redistributed source artifacts.

## Validation and limits

The 6-second isolated DMA-BUF check at 1080p60, H.264 VA-API, 6 Mbps passed with
the corrected pinned module:

- Source, native capture and received output all remained **1920×1080**.
- 359 DMA-BUF callbacks and 351 unique received frames; every received timing
  marker validated, with no duplicate measured frames.
- Native GPU import was observed; missing-rendition fallback count was **zero**.
- Encoder reported `amd: vaapi`, native-handle support and hardware acceleration.
- No CPU surface creation was reported in the retained native log.

Report: `out/capture-implementation-20261102/dimensions-1080p60/report.json`.
The test adds this exact candidate hash to `capture_scope.REVIEWED` only in its
private Python process; it did not temporarily broaden production acceptance.
The installed application, account, host portal and display were not involved.

`tests/test_capture_dimensions.py` passes four focused tests, including execution
of the actual original/replacement x86 instructions in a separate process. It
checks standard resolutions, ultrawide, portrait, the full 0–4096 dimension
domain, hash rejection, write failure, hardlinks, permissions and idempotence.
The modified Python files pass Ruff lint and formatting.

Follow-up integration checks pass: 9 HRD/pipeline tests, 4 dimension tests,
20 updater tests, 4 packaging tests and 11 CI-selection tests. The pipeline tests
cover both module layouts from original, HRD-corrected and fully corrected states,
plus rejection of a partial dimension-only edit and second-stage failure before
creating any staging output. Full repository checks run centrally after parallel
work is integrated.

This verifies the reproduced dimension/fallback defect on this AMD hardware. It
does not establish a general throughput improvement, fix every bitrate reduction,
or validate all codecs, driver vendors, real portal selections and network paths.
The fused shader still writes an I420 staging buffer alongside NV12; removing
this fallback must not be described as eliminating every copy or CPU mapping.

### Native GPU rendition limit

Further inspection of the pinned module establishes that
`cc_video_frame_get_rendition` at `0xe25c50` is `xor eax,eax; ret`: it always returns
null. The `SetRequestedRenditions` message handler at `0xe90d70` deallocates the
received set without updating capture state or generating additional surfaces.
This means additional GPU renditions are not implemented through that API.

The primary GPU path can still scale a large source to the configured output:
`LinuxVideoSource::UpdateCapturerLocked` uses the optional sink target pixel
count and `GetScaledResolution`, then sends `UpdateConfig`. That message copies
width and height into capture state, which `calculate_pool_resolution` uses for
its one GPU-scaled output. Thus a larger source does not inherently require CPU
scaling. Later investigation below found that the separate maximum pixel count
was ignored: the original assessment that all sink pixel limits were honored
was incomplete.
Different source/output sizes require hardware validation of the configured cap;
additional simulcast sizes and asynchronous adaptation can still reach CPU
`CropAndScale`. Saved disassembly: `native-get-rendition.txt`,
`native-requested-renditions.txt`, `native-capture-message-handler.txt`.

### Stronger bounded contention fixture

`tests/screenshare/pressure_session.py` adds `both` mode: one CPU worker per
inherited affinity CPU plus one GPU worker. It rejects unbounded sessions,
non-PipeWire backends and missing explicit GPU devices. A partial worker startup
or exceeded controller deadline cannot pass validation, and cleanup never starts
load after early client exit.

The old GPU-only load produced roughly 40,000 dispatches in 10 seconds at about
80% reported GPU busy. `pressure.c` now does 4096 arithmetic iterations instead
of 256 per invocation, with the same 4 MiB buffer and only one 1024×1024 dispatch
in flight. This amortizes submission gaps without an accumulating command queue.
It reports dispatch wall time and has an independent process alarm that bounds
initialization and driver waits as well as the existing workload deadline.

Eight pressure-controller/compile tests pass; the GLSL also passes offline
`glslangValidator -S comp`. These checks do not establish utilization or a
performance result for the stronger workload; those belong to the subsequent
centrally scheduled hardware comparisons.

## Separate AMD codec alignment restrictions

[GPU Screen Recorder's VA-API encoder][gsr-vaapi] distinguishes codec restrictions
from capture sizing. Its H.264 path uses even dimensions. AMD HEVC uses 64-pixel
width and 16-pixel height padding unless the driver advertises suitable alignment
support. Its AMD AV1 path still has a width-64/height-16 workaround, except that
1080 height is special-cased; the source notes newer VCN hardware may not need it.

These are not reasons to distort every captured frame to multiples of 64. Codec
capability negotiation and any codec-specific padding/crop handling must be
validated independently, with actual native encoder initialization and decoded
dimensions. Do not advertise AV1 as usable solely because a VA-API entry point
exists. Do not silently expose padded aspect ratios in the stream UI.

The [installed Mesa 26.2.5 encoder source][mesa-vcn] was fetched again from its
versioned upstream URL and exactly matches the retained source. Its
`radeon_vcn_enc_av1_get_session_param` distinguishes VCN versions: before VCN 5,
coded width aligns to 64 and height to 16, except heights divisible by 8 but not
16 use `height + 2`. VCN 5 instead aligns coded width to 8 and height to 2.
The function permits smaller input surfaces within specified padding bounds and
tracks padding separately. The [VCN 4 command writer][mesa-vcn4] writes aligned
coded dimensions to AV1 headers and writes separate render dimensions only when
that option is enabled. This is a real codec-specific constraint; capture pitch
and visible output remain different concerns.

The AV1 loopback subsequently confirmed a second defect: capture/native frames
remain 1920×1080 with zero missing GPU renditions, but AMD VA-API AV1 decodes to
**1920×1082** through dav1d. The software receive report is
`out/codec-research-20261102/av1-1080p30-software/report.json`: 176 unique frames
in six seconds, all synthetic markers intact, and the exact-dimensions check
fails. The sender's dimensions statistic alone incorrectly appears healthy.

The [AV1 specification's render-size semantics][av1-semantics] explicitly make
render dimensions a display hint that does not affect decoding. Simply changing
`render_height` is therefore insufficient for the actual decoded image contract.
Blindly rewriting coded height also changes MI/tile geometry. No header-only
correction was established. FFmpeg 8's VA-API surface-alignment query chiefly
explains GSR's HEVC allocation improvement; inspected FFmpeg AV1 code and GSR's
AV1 workaround do not establish a fix for this native 1082 output.

### Scoped native encoder compatibility guard

`integration/video/encoder.c` wraps the reviewed `cc_encoder_create`,
`cc_encoder_update` and `cc_encoder_destroy` linkage slots. The C++ DWARF defines
the exact by-value C ABI: `SessionOptions` is 40 bytes, `EncodeOptions` is 24,
AV1 is codec 2, VA-API types are 5/6, and actual AMD vendor is 1. Both original
module versions were inspected; the six original/HRD/dimension-corrected hashes
have an independent allowlist in `scripts/encoder_scope.py`. No Rust object
layout is inferred or modified, and no encoder payload is relabelled.

The guard first lets the real encoder initialize, then checks its actual vendor.
It applies only if the kernel reports UVD/VCN major 4 for **every AMD render
device**. The [kernel IP-discovery implementation][kernel-discovery] exposes
these values under `ip_discovery/die/*/UVD/*/major`; this host reports 4.0.0.
Missing, mixed-generation or newer AMD data retains upstream behavior. This
conservative hardware rule avoids guessing a selected GPU from an unrelated
display device or restricting newer VCN hardware.

For an affected AMD VA-API AV1 encoder, non-64×16 actual geometry fails through
the normal native create/update failure path before an invalid-size frame is
encoded. The requested source/resolution remains unchanged, and aligned AV1
continues through the original backend. Update interception also covers an
aligned stream later resized to an unsafe geometry. Creation rejection destroys
the just-created encoder and clears its output handle; destruction removes
tracking so a reused handle cannot inherit an old AV1 restriction.

The helper exposes rejection counts, dimensions and
`amd-av1-coded-size-padding` in its diagnostics. Installation checks the full
module SHA, ELF linkage targets and actual exported functions before changing
three aligned data pointers. It runs before VoiceEngine initialization, with
rollback on installation failure. Original page permissions are restored:
these modules' `.got.plt` slots are outside GNU_RELRO and originally writable,
so forcing them read-only would break unrelated lazy symbol binding.

The first runtime installation correctly rejected an unresolved PLT binding:
`dlsym` and `dlopen(RTLD_NOW | RTLD_NOLOAD)` do not reliably replace an already
loaded library's lazy GOT entries. Scope metadata now records each exact
reviewed lazy resolver address too. Installation validates its `jmp [slot]`,
relocation push and jump to the module resolver; only the actual symbol or
that exact stub may occupy the slot. Rollback restores whichever original
state was present. A separately compiled shared library loaded with
`RTLD_LAZY` exercises the real installer without Discord or GPU initialization.

Focused tests exercise the real C wrapper's ABI forwarding, create/update
failure, retained aligned AV1, other codecs/vendors/backends, destruction and
handle reuse, plus eight synthetic sysfs layouts and JavaScript integrity
rejection, plus real lazy-linkage installation. Both real module versions
produce reviewed scope metadata. The actual 1080p guard test subsequently passed:
one rejection, genuine `setOnVideoEncoderFallbackCallback("AV1")`, explicit
H.264 payload negotiation, then 173 unique received 1920×1080 frames. Report:
`out/codec-research-20261102/av1-1080p30-guard-lazy/report.json`. No synthetic
fallback callback or decoder-validation bypass was needed.

### Correct native AV1 level signalling

Aligned 1280×720 AV1 exposed a separate defect: the encoder declared
`seq_level_idx = 0` (level 2.0), whose maximum picture area is 147,456 pixels.
The native image contains 921,600. Chromium/libgav1 correctly rejects that
sequence; dav1d and FFmpeg's VA-API path had tolerated it. Changing only the
sequence level to index 5 in a synthetic captured stream enabled Chromium's
hardware decoder, establishing the cause without weakening receiver checks.

The reviewed native `create_sequence_header` constructor initializes its first
operating-point level at output byte `+0x36` to zero. The VA-API parameter builder
and native OBU serializer both consume that same field. Upstream cros-codecs now
sets index 23 with a TODO to estimate the level, but such a broad declaration
would constrain interoperability unnecessarily.

The helper now intercepts only the two full-hash-reviewed direct calls to that
constructor. It preserves the original three-pointer calling convention and
all original output except the first operating-point level byte. The native
serializer remains responsible for the tier bit required for indices above 7;
no already encoded OBU or tile data is rewritten. This correction applies to
both cros-codecs VA-API paths regardless of GPU vendor. The separate AMD VCN4
dimension restriction remains scoped to that affected hardware.

FPS and rate control come from the constructor's actual config, independent of
the calling thread. Native `Tunings::Debug` field names and pointer offsets
identify framerate at `+0x30` and rate control at `+0x18`; both reviewed builds
have the same layout. Rate-control Debug and consumers establish CBR tag 0 with
bitrate at `+0x20`, VBR tag 1 with maximum at `+0x28`, and unbounded constant
quality tag 2. Visible dimensions come from the already constructed header's
width/height-minus-one fields at `+0x2d4/+0x2d6`.

`av1-level.h` selects the lowest applicable Main-tier level using picture area,
horizontal/vertical limits, pixel display rate, maximum configured bitrate and
frame-header rate. The native encoder uses one tile/column, below every selected
level's tile limits. This follows the practical constraints used by
[FFmpeg's level selector][ffmpeg-levels]; it does not certify instantaneous
compression-ratio or every decoder-model constraint. Malformed, unbounded or
out-of-table configurations use the specification's unconstrained index 31
instead of falsely claiming a lower bound.

The native in-place update path retains its sequence header: `VaapiEncoder::update`
calls `create_tunings` and the predictor's `tune` queues new values, without
calling the sequence constructor. To keep its declaration valid over the
encoder's lifetime, creation also includes the requested session FPS and maximum
bitrate ceilings. The reviewed call chains are synchronous: `cc_encoder_create`
directly calls the selected VA-API constructor, which directly calls
`new_native_vaapi`, then `create_sequence_header`. For the pinned Vulkan path the
call sites are `0xe2a976 → 0xe569f0`, `0xe57992 → 0xe5bec0` and
`0xe5bfe6 → 0xe51260`; the AMD path has the same direct structure.
A nested save/restore context passes those additional ceilings. Actual config
values are still read directly, and a missing expected constructor observation
fails through native fallback rather than guessing a level.

All AV1 VA-API handles, regardless of vendor, retain the emitted level in helper
tracking. Within-level FPS/bitrate updates continue normally. An update that
would exceed that level fails through the native codec-recovery path before
invalid headers are sent; diagnostics report `av1-level-limit`. This deliberately
uses normal recovery instead of mutating live Rust predictor/sequence state.
A 60 FPS session initially adapted to 30 therefore declares its 60 FPS ceiling
and can recover without fallback. A separately created 30 FPS session raised
in place beyond its level uses normal fallback. Zero-rate suspension is allowed;
resumed nonzero settings are checked again.

Focused tests cover 720p30/60, 1080p30/60, 1440p60, 4K30/60, 4096×4096 at 60,
bitrate/header-rate thresholds and unbounded input. A byte-for-byte output test
confirms that only `+0x36` changes for CBR/VBR/constant-quality configurations.
Lifetime tests cover 720p30 → 60 rejection, bitrate increases, Intel handles,
nested construction context, missing constructor observations and a 60 FPS
session recovering from an initial 30 FPS without rejection.
Both real modules pass constructor/call metadata inspection; original RX page
permissions are restored after installation.

The first real corrected 720p30 native RTP → Electron hardware-decoder run
passed: index 5, 30 FPS and 6 Mbps reported from the actual config, one corrected
sequence, no codec errors or fallback. The production transport wrapper and idle
cleanup checks also passed. Artifact:
`/mnt/storage/dpe-screenshare-final-20261102/validation/av1-720p30/report.json`.
The coordinated 1440p60 and 4K30 Source runs also passed actual Electron hardware
receive at index 12, with no codec errors or size rejections. Reports are in
the adjacent `av1-1440p60` and `av1-4k30-source` validation directories. The final
lifetime helper passed a 12-second 720p cadence test, 30 → 10 → 30: three
corrected sequences, index 5/FPS 30/6 Mbps, actual Electron hardware decode
throughout, and no rejections, codec errors or fallback. Report:
`/mnt/storage/dpe-screenshare-final-20261102/validation/av1-720p30-final/report.json`.
The tested final helper SHA-256 is
`e9f545554afa194d4d582a357d32fc8a029aef31d0f93850c1023c67b48d0670`.

[Discord's AMD investigation][discord-amd] also describes a separate feedback
loop between frame dropping, encoder reconfiguration and rate control. That
article concerns AMD Go Live behavior and shows Windows results; it does not
prove that the same issue is the cause of this Linux native capture defect.

## Mutter issue 4563

The [official issue][mutter-issue] remains open as inspected on this date. It
reports NVIDIA/GNOME black bands and stale regions near the top of screencasts,
especially with changing frames or GPU load. It is a producer/consumer
synchronization problem, distinct from the AMD dimension defect above.

The public GitLab issue API and related-merge-request API were readable. HTML
was blocked, and issue discussion endpoints returned 401; this review therefore
does not claim to have read every discussion comment. Retained API descriptions
are in `out/capture-implementation-20261102/mutter-4563-mrs.json`.

Two relevant proposals were still open:

- [Mutter !5433][mutter-wait] holds a DMA-BUF until the GPU fence signals when
  NVIDIA is used and no `SPA_META_SyncTimeline` was negotiated. It queues from
  the main loop asynchronously, with a blocking finish fallback only when no
  fence descriptor is available. Its author reports reduced corruption and
  successful churn tests; those are upstream reports, not our hardware results.
- [Mutter !5443][mutter-shm] rejects DMA-BUF modifiers for NVIDIA consumers that
  did not negotiate explicit synchronization, causing shared-memory fallback.
  Other drivers and explicitly synchronized consumers retain DMA-BUF. This
  trades performance for correctness on the affected combination.

The already-merged [!4725][mutter-gbm] reduces NVIDIA blocking in GBM front-buffer
locking; its presence in related merge requests does not mean issue 4563 is fixed.

The host runs the `urayde/niri` fork, not Mutter, on AMD. Its exact inspected
commit is `3af9296f088547de86c4ad19f9b293d94dea49b5`.
[That source][host-niri] routes successful frame rendering through
`queue_after_sync`, asynchronously waiting for exported fence readiness before
queueing. This is the approach introduced by [niri PR 2175][niri-wait], merged
4 August 2025 after affected NVIDIA users tested it. The host source still has a
fallback that queues when fence export is unavailable; no failure of that path
has been demonstrated on this machine.

No host compositor replacement or blanket memfd fallback is justified by this
research. A consumer-side DMA-BUF import flag cannot recover a producer fence
that was never attached. Useful remedies on an affected GNOME/NVIDIA system are
the compositor fence-wait fix or truthful explicit-sync negotiation across the
producer and consumer; shared memory is a scoped correctness fallback. Switching
capture APIs only helps if the replacement owns a correct completion/release
contract. No claim is made that changing APIs alone fixes the issue here.

## Actual host compositor verification

A bounded external fixture also exercised the running niri compositor, rather
than the private synthetic PipeWire producer. A dedicated Wayland application
draws timestamp markers; its unique app ID, title and owning PID must match
before passing its exact window ID to niri's Mutter-compatible `RecordWindow`.
The resulting host PipeWire node is supplied to the existing isolated native
sender through a private portal adapter. This measures real compositor capture
and DMA-BUF import, but does not validate the public GNOME picker or Discord SFU.
The public portal has no supported exact-window-ID selection option.

The broker explicitly stops its own session and closes its own windows and
process groups. An independent deadline watchdog can repeat that cleanup if
the broker fails. No desktop, unrelated window, image or packet recording is
used. This Electron build adds a measured 20 pixels to the captured surface;
1260×700 content produces a 1280×720 surface. Adjusting only the synthetic
fixture's content dimensions avoids attributing that outer surface to encoding.

The 720p30 check delivered 171 unique 1280×720 frames in six seconds through
native AV1 encoding and Electron hardware decoding, without codec errors or
fallback. The dynamic level correction selected index 5. A subsequent pair
used a 1920×1080 captured surface, a 240 FPS source timer, H.264 hardware encode
and Electron hardware decode. Each 18-second run contains six seconds of
combined pressure across all 20 CPUs (affinity 0–19) plus the GPU.

| Observation                                     | Original reviewed module, capture observation only | Corrected module, paced capture |
| ----------------------------------------------- | -------------------------------------------------- | ------------------------------- |
| Decoded dimensions throughout                   | 1680×952                                           | 1920×1080                       |
| Native GPU conversions over 18 seconds          | 4066                                               | 1080                            |
| Frames skipped before native GPU conversion     | 0                                                  | 3030                            |
| Missing GPU rendition / CPU scaling fallbacks   | 1070                                               | 0                               |
| Encoded FPS during pressure                     | 59–60                                              | 60–61                           |
| Actual bitrate during pressure                  | 5.54–6.02 Mbps                                     | 4.95–5.54 Mbps                  |
| Unique viewer frames during six-second pressure | 317                                                | 325                             |
| Pressure CPU / GPU median utilization           | 100% / 96%                                         | 100% / 95%                      |

Both receivers passed delivery checks. The original module reproduced the
resolution loss and conversion waste on the actual compositor, but did **not**
reproduce the reported severe bitrate collapse. Different decoded resolutions
prevent treating bitrate or latency differences as an equal-quality benchmark.
The preliminary 1080p run omitted the fixture's OpenH264 fallback dependency
and failed receive initialization; it is retained as a failed control. Native
`PixelCappedDecoder::Configure` calls both decoder initializers and returns the
low-resolution decoder's result even when the hardware initializer succeeds.
Restoring the existing hash-verified OpenH264 parameters restored hardware
viewing without a production decoder change.

Evidence is retained under
`/mnt/storage/dpe-screenshare-final-20261102/validation/`: the successful
`host-niri-720p30-surface` and paired
`host-niri-1080p60-full-{baseline,candidate}` directories contain reports,
`host-summary.json`, native logs, pressure telemetry and cleanup evidence.
The pair uses original native hash `2352578d…` and corrected `72096efc…`.

[gsr-vaapi]: https://git.dec05eba.com/gpu-screen-recorder/tree/src/encoder/video/vaapi.c
[discord-amd]: https://discord.com/blog/from-blocky-to-brilliant-improving-video-quality-on-discord-go-live-on-amd-gpus
[mesa-vcn]: https://gitlab.freedesktop.org/mesa/mesa/-/blob/mesa-26.2.5/src/gallium/drivers/radeonsi/mm/radeon_vcn_enc.c
[mesa-vcn4]: https://gitlab.freedesktop.org/mesa/mesa/-/blob/mesa-26.2.5/src/gallium/drivers/radeonsi/mm/radeon_vcn_enc_4_0.c
[av1-semantics]: https://github.com/AOMediaCodec/av1-spec/blob/master/07.bitstream.semantics.md
[kernel-discovery]: https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/amd/amdgpu/amdgpu_discovery.c
[ffmpeg-levels]: https://github.com/FFmpeg/FFmpeg/blob/master/libavcodec/av1_levels.c
[mutter-issue]: https://gitlab.gnome.org/GNOME/mutter/-/work_items/4563
[mutter-wait]: https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/5433
[mutter-shm]: https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/5443
[mutter-gbm]: https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/4725
[host-niri]: https://github.com/urayde/niri/blob/3af9296f088547de86c4ad19f9b293d94dea49b5/src/screencasting/pw_utils.rs
[niri-wait]: https://github.com/niri-wm/niri/pull/2175

## Experimental producer frame-rate negotiation

The native capture path advertises a variable `framerate` range but omits
`SPA_FORMAT_VIDEO_maxFramerate`. The host's
[niri capture implementation](https://raw.githubusercontent.com/urayde/niri/3af9296f088547de86c4ad19f9b293d94dea49b5/src/screencasting/pw_utils.rs)
advertises a maximum and uses the negotiated maximum to avoid rendering capture
frames too soon. The preceding host tests still received approximately 240
callbacks per second with a 60 FPS share: downstream admission reduced GPU
conversion work but did not reduce compositor capture production.

`DPE_CAPTURE_UPSTREAM=1` enables an experimental helper independently of the
normal downstream gate. Exact native hashes, relative GOT relocations, call
instructions and C dispatch stubs are checked before installation. The helper
uses public PipeWire C interfaces and serialized SPA properties; it does not
modify Rust object layouts. Initial offers remain unchanged. A cap is offered
only after the producer selects a raw Format with variable framerate and an
explicit positive maximum. Fixed-rate and unknown producers remain unchanged.
Original modifiers and unrelated parameters are preserved. Native modifier
updates pass through unchanged; only the latest accepted native offers are
used for subsequent cap requests.

The helper's owner-loop timer handles rate changes without waiting for another
captured frame. `update_params` success means only that an offer was published:
`appliedFps` becomes nonzero only after a matching negotiated Format. Missing
acknowledgment after two seconds disables producer capping for that stream and
attempts the saved native offers once. Restoration is counted only after a
Format acknowledgment matching the originally observed uncapped maximum. A late
capped reply cannot acknowledge restoration; a missing or changed baseline
remains unacknowledged. Synchronous errors, asynchronous ERROR notifications,
missing acknowledgments and pending rates have separate diagnostics.

This is deliberately **opt-in**, because recovery from every producer failure
has not been proven. In
[PipeWire 1.6.10](https://raw.githubusercontent.com/PipeWire/pipewire/1.6.10/src/pipewire/stream.c),
republishing offers cannot reset a genuinely ERROR stream. Some negotiation
errors belong to the link rather than the stream. The reviewed native ERROR
callback at `0xe8e270` also sends a `CaptureSourceMessage` to its owner, so this
helper does not attempt to race that callback with an unproven reconnect. A
failed experimental negotiation can require restarting the share. The normal
capture pacing path remains enabled without this experiment.

Focused C checks cover exact property/modifier preservation, unknown and fixed
producers, 60/30/zero transitions, delayed and mismatched acknowledgment,
missing-ack timeouts, terminal ERROR, nested native offer updates, and destruction
inside an update callback. The same fixture passes ASan and UBSan. JavaScript
checks verify that missing helper exports and native scope/hash mismatches fail
before native mutation.

The bounded actual-host proof is retained at
`/mnt/storage/dpe-screenshare-final-20261102/validation/host-niri-upstream-rates/`.
It uses the same dedicated-window broker and independent Stop watchdog as the
preceding host tests, with a 240 Hz synthetic source and no stress workers.
During twelve seconds, the requested rate changes from 60 to 30 and back to 60;
then an explicit zero-rate request is observed for another 1.3 seconds before
stopping capture. The source remains animated throughout.

- Actual native producer callback increments settle at 60–61/s, then 30/s,
  then 60/s. Negotiated maxima are exactly 60/1, 30/1 and 60/1.
- Removing the limit restores the producer's 239761/1000 maximum and yields
  another 311 callbacks in 1.3 seconds, approximately 239/s. This demonstrates
  producer negotiation, rather than downstream frame dropping alone.
- At twelve seconds the actual Electron hardware receiver has decoded 593
  frames. Every measured frame remains 1920×1080. AMD VAAPI H.264 encoding and
  Electron hardware decoding remain active, with no codec error/fallback,
  negotiation error, acknowledgment timeout, or invalid frame marker.
- The first twelve seconds contain 612 callbacks and 597 GPU conversions;
  only 15 frames need downstream pacing, including startup and transitions.
  No missing-rendition CPU fallback is reported.
- Explicit native offers restoration is acknowledged before Stop. The immediate
  fixture report still observes one native stream while asynchronous teardown
  is pending; it is not evidence of synchronous native destruction. The broker
  then stops the compositor cast and terminates its owned process groups.
  Both owned PIDs are gone and the independent watchdog did not fire.

The tested helper SHA-256 is
`9fa3892cd496dc6c8c9be5596e8cc3b9e4488f342eec56a966cad35e97ecb526`.
After this run, a focused regression tightened restoration acknowledgment to
require the original observed uncapped maximum, rather than any raw Format.
The recorded 239761/1000 restoration satisfies the stricter predicate. This
small diagnostic guard was checked with the C fixture and ASan/UBSan; the
hardware run above used the preceding helper hash.

This establishes useful producer-side savings on this niri implementation. It
does not establish generic producer ERROR recovery, equivalent behavior on
other compositors, or a CPU/GPU utilization improvement under matched load.

## Resolution adaptation was ignored by the Linux GPU capture source

A more faithful reproducer uses production screenshare degradation preference
`MAINTAIN_FRAMERATE`, production transport starting rates, and a scrolling
high-detail synthetic texture. The earlier balanced-degradation fixture could
reduce requested FPS and did not exercise this path reliably.

WebRTC's `VideoStreamAdapter::DecreaseResolution` sets a maximum pixel count and
clears the optional target count. Conversely, increasing resolution sets a
target and a larger maximum. The
[sink controller](https://webrtc.googlesource.com/src/+/refs/heads/main/video/video_source_sink_controller.cc)
forwards those independently. The retained WebRTC source documents the
asymmetry in `call/adaptation/video_stream_adapter.cc:145` and implements the
downward transition at line 485.

The reviewed native `LinuxVideoSource::UpdateCapturerLocked` reads only
`VideoSinkWants.target_pixel_count` at offset 8 and passes it to
`GetScaledResolution`. It ignores `max_pixel_count` at offset 4. Therefore a
normal downward request keeps the original GPU dimensions. In the matched
reproducer the quality scaler requests 1,244,160 pixels, but the producer remains
1920×1080. Subsequent requests report `kAwaitingPreviousAdaptation`; the encoder
rate limiter drops frames while the capture source never acknowledges the
requested smaller input. This concrete failure does not require a missing GPU
rendition or CPU scaling fallback.

`capture-scale.c` redirects only the reviewed scaled-resolution call inside
`UpdateCapturerLocked`. Its short dispatch preserves the native optional target,
passes the already constructed local sink wants from `r15`, and retains the
normal SysV two-integer result in `rax`/`rdx`. It fills or clamps the target using
a finite maximum, then invokes the original scaled-resolution function. Reduced
results are rounded down to even dimensions so the native NV12 pool's subsequent
round-up cannot exceed the pixel budget. The no-sink call, unbounded Source,
configured source maxima and other capturers remain unchanged. GPU allocation,
aspect-fit, synchronization, encoder selection and WebRTC's adaptation policy
continue through the original implementation.

Scope generation checks all six known original/HRD/dimensions module hashes and
the complete parent-function hash for each of the two native revisions. The
installer additionally verifies the exact 90-byte live-register context and
relative call target, installs before voice initialization and restores RX code
protection. Unknown modules or missing exports retain native behavior. Focused
C checks execute the actual emitted register dispatch under ASan/UBSan, cover
max-only restrictions, target clamping, unbounded Source, and the existing-target
edge `max=target=921599`: its reduced rectangle stays within the maximum even
after NV12's even-rounding step. JavaScript checks reject mismatched scope,
invalid offsets and incomplete helpers before native mutation.

The matched reports are retained under
`/mnt/storage/dpe-screenshare-adaptation-20261102/`:

| 18-second scrolling-source run                    | Without the new scaling correction       | With the scaling correction                                 |
| ------------------------------------------------- | ---------------------------------------- | ----------------------------------------------------------- |
| Native adaptation                                 | Remains 1920×1080 awaiting prior request | GPU pool progresses 1486×836 → 1148×646 → 888×500 → 686×386 |
| Final encoded FPS                                 | 5                                        | 58                                                          |
| Receiver unique frames, six-second pressure phase | 38                                       | 75                                                          |
| Receiver unique frames, final six seconds         | 32                                       | 151                                                         |
| Missing GPU rendition / CPU fallback              | 0                                        | 0                                                           |

Both runs use the same corrected native module, 1080p60 request, 240 Hz synthetic
DMA-BUF source, production transport profile, hardware H.264 encoding and
Electron hardware receive. The middle six seconds apply bounded CPU pressure on
all 20 logical CPUs plus GPU pressure. The scrolling texture continues during
the final phase, so “recovery” there means pressure removal, not simpler content.
The scale correction is the behavioral difference between these two runs;
reports are `scroll-production-pressure/report.json` and
`scroll-production-pressure-scale/report.json`.

This demonstrates a persistent adaptation stall and its correction. Maintaining
cadence requires a smaller encoded image for this deliberately difficult texture
and bitrate. It is not a claim of unchanged image quality or a proof that every
bitrate decrease has this cause. The separate no-op additional-rendition API
remains unimplemented; this fix makes the existing primary GPU scaler honor the
restrictions it was already intended to receive.

### Final recovery and Source-mode controls

After the pixel-limit correction below and its odd-target regression fix, the
30-second `scroll-settle-recovery` check switches from the detailed texture to a
quiet image at nine seconds. The native output first adapts to 1486×836 and
1148×646. It then recovers through 1480×832 to 1920×1080 by the 24-second sample,
maintaining 60–61 encoded FPS after the content settles. There are zero invalid
sink-wants observations or missing-rendition CPU fallbacks. Lower media bitrate
on the quiet image is expected content-dependent rate control, not failure.

The six-second `source-ultrawide` control requests Source at 3440×1440/15 FPS.
Every decoded frame remains exactly 3440×1440. The adapter observes seven native
calls and applies no finite limit, preserving Source's original path and aspect
ratio. AMD VAAPI encoding and the Electron receiver pass; the targeted receiver
log also records the VAAPI decoder. These controls and the matched pressure pair
are under `/mnt/storage/dpe-screenshare-adaptation-20261102/`.

The final helper `8df8f9658a16913c8ca1abc73a89f77e21b984047ac451c145a26ea4139a91dc`
matches both controls and the compatible initial-preference niri test. The
earlier matched pressure correction used `c6d19c173727…`, before the stricter
already-present odd-target guard. That exact edge is covered by the C/ABI fixture
and sanitizers. The original severe bitrate collapse is still not reproduced;
these checks establish the separate, reproducible resolution-adaptation stall
and its recovery.

## Native statistics audit: estimate, configured limit and actual output

The following mappings were checked against the symbol-bearing native module
`out/capture-implementation-20261102/hrd-baseline/discord_voice.node`, SHA-256
`2352578d855e5ff75c0433fe8ca0ffa6d599691a506a7908da3d0d6523cc91b8`.
Addresses below are ELF virtual addresses for that exact revision. The capture
and HRD corrections do not alter these functions. Retained WebRTC source under
the `be92ed7160913c8b53ae06e6003e6de864d292f1199af9420a8c6dd22ec72aa4`
reference checkout supplies the upstream definitions; native serialization was
checked separately because it changes some meanings.

| Native JSON field                            | Verified meaning and diagnostic limit                                                                                                                                                                                   |
| -------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `transport.sendBandwidth`                    | Latest `Call::Stats::send_bandwidth_bps`, in bits per second. This is the connection's congestion-controller target estimate, subject to configured bounds, not measured upload capacity or one video's encoder target. |
| `transport.outboundBitrateEstimate`          | Last bitrate recorded by either a delay-based or loss-based BWE event. It can describe an internal estimator stage or remain stale; it is not interchangeable with `sendBandwidth`.                                     |
| `outbound.video.targetMediaBitrate`          | For an active media substream, native serialization overwrites the original encoder target with the RTP sender's total bitrate. The field name is misleading.                                                           |
| `outbound.video.mediaBitrate`                | For that active substream, total RTP sender bitrate minus retransmission bitrate. This is outgoing traffic, not exclusively encoded-picture bytes.                                                                      |
| `framesDroppedRateLimiter`                   | Cumulative media-optimization frame-dropper count.                                                                                                                                                                      |
| `framesDroppedEncoderQueue`                  | Cumulative encoder-queue drop category, including some native frames discarded while paused or too large for the target bitrate. It does not uniquely identify CPU overload.                                            |
| `framesDroppedCongestionWindow`              | Cumulative congestion-window pushback drops.                                                                                                                                                                            |
| `bwLimitedResolution` / `bwLimitedFrameRate` | Quality-adaptation flags, also affected by disabled layers or internal scaling; they do not independently establish network congestion.                                                                                 |
| `avgEncodeTime`                              | CPU-overuse observer's filtered integer-millisecond measurement. A reported zero does not establish zero encoding latency.                                                                                              |

`Connection::GetStats` reads the Call bandwidth result at `0x392a8e`.
`Call::GetStats` obtains that value from its `+0x670` field at `0x67a52e`;
`Call::OnTargetTransferRate` writes it at `0x67aaad`. By contrast,
`Connection::Log` at `0x398462` copies the bitrate from event types 7 and 8
(`RtcEventBweUpdateDelayBased` and `RtcEventBweUpdateLossBased`) into the separate
field serialized as `outboundBitrateEstimate` at `0x392bdf`.

`LocalUser::GetStats` initially serializes the whole-stream WebRTC target and
media rates at `0x3c6c6a` and `0x3c6ca7`. When media substreams exist, it copies
that base object, then replaces `targetMediaBitrate` with
`StreamStats::total_bitrate_bps` at `0x3c77f4` and `mediaBitrate` with total minus
retransmit at `0x3c782d`. Finally `0x3c7a0c–0x3c7a78` publishes both the
`videos` array and `video = videos[0]`. Before any media substream exists, the
base object is used instead. Consequently the misleading target field can even
change semantics across startup; the settings panel must not label it an
encoder/controller target.

The upstream
[RTP sender accounting](https://webrtc.googlesource.com/src/+/main/modules/rtp_rtcp/source/rtp_sender_egress.cc)
tracks complete RTP packet sizes over a one-second window. Subtracting
retransmissions does not remove RTP headers, padding or forward-error-correction
traffic. “Outgoing video bitrate” is appropriate; “encoder output bitrate” is
too specific. The three discard counters come from whole-`VideoSendStream`
statistics and are copied into its media-stream objects. They are cumulative,
may reset when the stream is recreated, and must not be summed across those
copies. The
[statistics observer](https://webrtc.googlesource.com/src/+/refs/heads/main/video/send_statistics_proxy.cc)
maps the named drop reasons and derives the quality flags; the field names alone
are not a diagnosis of the cause.

The `scroll-production-pressure-scale/report.json` samples report
`avgEncodeTime=0` and `encodeUsage=0` throughout. The same run's
`discord-webrtc_0` records `WebRTC.Video.Screenshare.EncodeTimeInMs 57`. These
come from different measurement paths: `OnEncodedFrameTimeMeasured` updates
the CPU observer's filtered value, whereas the histogram uses encoded-image
finish minus start timestamps. The audit establishes that zero is unreliable
here; it does not establish why the CPU observer remains zero. Do not display
it as a measured zero-millisecond encode latency.

The configured video maximum is separately observed from the last accepted
`encodingVideoMaxBitRate` option. Report it only when positive. The native
wrapper accepts zero as an engaged integer (`0x2fec2d`), while
`CreateDefaultVideoSettings` supplies a positive 2.5 Mbps maximum
(`0x3fa358`). A universal zero-means-unlimited convention was not established;
zero should be shown as unknown rather than a literal zero ceiling or unlimited.

The pressure-scale native log also contains real transport-wide congestion
feedback, probing and a startup bandwidth ramp. Loopback therefore exercises
feedback; its configured 10 Mbps ceiling and local delivery do not reproduce
Discord SFU behavior or an internet bottleneck. Lower actual output, a quality
flag or a discard count alone cannot establish another bitrate-collapse cause.
