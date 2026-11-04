# Screenshare degradation investigation, 1 November 2026

Status, updated 2 November: **H.264 correction and optional diagnostic-copy
reduction integrated into local builds and updater module preparation;
effect on viewer performance not yet established**. The user reports
viewer-visible lag and falling bitrate in both window and monitor shares at
1080p30/60, especially with motion or CPU/GPU load. Local preview behavior is
unknown. Do not interrupt their current Discord session.

The user explicitly requested completing implementation without end-to-end launch
tests. This integration changes repository/package inputs, not the running client
or its installation. Hardware/load and launch validation remain deferred.

The subsequent [capture-method research pass](capture-methods-20261102.md)
compares current GSR, vkcapture, direct Wayland, PipeWire and other implementations,
including this machine's niri fork and the native GPU-frame integration boundary.

## Evidence from the running installation

Read-only `/proc` mapping inspection identified the loaded voice module. Its
SHA-256 was
`f56d3c31611f769e6798bb2bc03ad2b5a21e2b29224825ca740af013e145f0d8`.
The older `out/pr1-audio-fixed-latest-voice` fixture has a different hash and is
not evidence of the current native implementation. No live process was attached
to, injected into, restarted or reconfigured. No host capture session, portal picker,
account login, outgoing message, call or physical-camera test was started.

Existing native RTC logs interleave webcams, screen shares, decoders and network
threads. Initial CPU surface/upload pool messages were **not attributable to the
screen share** after grouping by encoder thread. A `P8(Iyuv)` session format or
staging-pool allocation alone cannot prove that every frame uses CPU readback:
the installed `RustEncoder::Encode` also has a native GPU-frame branch calling
`cc_encoder_add_frame_native`. Native-handle capability is not proof of its use.

The sanitized observation captured in
`out/screenshare-analysis-20261101.json` contains a recent thread explicitly
configured with `content_type: kScreenshare`:

- The encoder reports hardware acceleration, native-handle support and an
  **untrusted rate controller**; the native initialization identifies VA-API.
- Three logged one-minute capture intervals each count 3,601 frames. These are
  capture inputs, not successfully transmitted or viewer-decoded frames.
- There are 197 bitrate frame-drop messages in that thread observation, four
  encoder-blocked messages, and three successful quality-scaler down-adaptations.
  These counters cover differing windows; do not turn them into a common drop
  percentage or add overlapping blocked-message and interval counters.
- Frame-drop messages report targets between 1,327,201 and 4,423,680 bits/s while
  the input is 60 FPS. Configured maximum bitrate is a separate quantity and
  cannot be substituted for the target the sender actually uses.
- The interval counters show no congestion-window-pushback drops. This does
  **not** rule out network congestion, bandwidth-estimator reductions or a sender
  scheduling delay: those are different mechanisms.

The logs establish bitrate-driven frame dropping and quality adaptation while
capture supplies the requested cadence in these intervals. They do not yet
establish the cause of the reduced budget or reproduce GPU saturation. A smooth
local preview would be consistent with this evidence but was not inspected.

A later bounded snapshot, `out/screenshare-analysis-active-followup.json`, still
shows the explicitly identified screenshare feeding capture at approximately
60 FPS across its logged intervals, with bitrate frame drops and quality-scaler
adaptation. The observed adaptation messages are from `QualityScalerResource`;
no `EncoderUsageResource` adaptation signal was found in this snapshot. Absence
of a CPU adaptation log is not proof that CPU scheduling or GPU contention is
irrelevant. These snapshots overlap and must not be added together.

Process-wide checks found successful bandwidth probes and modest logged pacer
delays, but those threads cannot be reliably joined to the screenshare from
these logs. They do **not** clear the network or establish a screenshare RTT/loss
measurement. Capture replacement remains a lower-priority lead than explaining
the outgoing rate-control/quality decisions.

## Architecture and why a flag change is insufficient

`integration/audio/app-audio.cjs` and `wayland-audio.cjs` wrap the native
connection's source and transport APIs. The current Wayland wrapper forwards
transport updates without audio fields unchanged; for audio updates it preserves
the other fields and replaces only the soundshare routing fields. No bitrate or
encoder override was found here. The source options are forwarded unchanged.

The installed native binary contains the Rust Linux PipeWire capturer, Vulkan
processing, DMA-BUF export, VA-API and NVIDIA encoder implementations. The runtime
logs show the Vulkan capture path and AMD VA-API encoder. Chromium's renderer GPU
flags and WebCodecs test results are separate from these native implementations.
Rebuilding Chromium's WebRTC would not change the separately shipped native voice
module's encoder or congestion controller.

Upstream [WebRTC's sender][sender] uses its frame dropper when the encoder's rate
controller is not trusted. The [frame dropper][dropper] accounts for encoded frame
size against the bitrate budget. High motion can increase encoded frame sizes;
the observed messages identify that mechanism, not whether the network estimate
or hardware rate control is wrong. The native Discord fork must still be tested
directly; the upstream source explains the mechanism, not an exact binary match.

Do not disable bandwidth estimation, frame dropping or quality adaptation as a
purported fix. That can turn intentional drops into growing queues or loss. Do
not force `AcceleratedVideoEncoder` in Chromium: it addresses another path and
the prior local [GPU audit](linux-gpu-flags.md) recorded a driver crash there.

### Extra frame consumers and diagnostic copies

Static inspection of the same native module found CPU-copy requests outside
the hardware encoder:

- `WumpusFrameBuffer::ToI420` (`0x4be7e0`) calls `ConvertToI420` with
  `CpuWants = 0`; that path calls `cc_video_frame_set_cpu_wanted`.
- `TryToI420` first checks for an available CPU frame and uses `CpuWants = 1`.
  `CloneAbgr` also distinguishes these modes; the mere existence of an ABGR
  accessor does not prove that it requests a new CPU copy.
- `ImageQualitySink::OnFrame` calls `CloneAbgr` with `CpuWants = 0` at
  `0x4520f3`. This optional measurement sink has VMAF/PSNR scorers, a redecoder
  and reconstruction-frame handling. It can add work even when the main encoder
  uses GPU-backed frames.
- `IsVideoQualityMeasurementEnabled` (`0x437590`) checks the nonempty
  `video-quality-measurement` codec parameter. This is distinct from WebRTC's
  bitrate/quality adaptation; it is not evidence that adaptation should be disabled.

The passive analyzer now counts measurement configurations on their native
encoder threads, without retaining opaque configuration values. In
`out/screenshare-analysis-copy-costs.json`, all observed VMAF/PSNR configurations
belong to `kRealtimeVideo` camera observations. The retained logs do not establish
whether the already-running screenshare enabled this sink. Configuration counts
are not scored-frame counts or measurements of copy cost.

`out/screenshare-frame-copy-calls.txt` records the relevant static call sites.
No running process was attached to and no pixels were inspected. A future
controlled comparison should distinguish preview consumers, optional quality
measurement and encoder fallback before attributing CPU copies to capture.
Neither preview nor diagnostic configuration was changed in the active session.

### Integrated diagnostic control

`integration/video/transport.cjs` now wraps native connections independently of
the application-audio setting. On Linux it clears nonempty diagnostic strings in
both `setTransportOptions.videoQualityMeasurement` and
`videoEncoder.params["video-quality-measurement"]`. Static inspection of
the field access at `0x2fed74` in `VoiceConnectionWrapper::SetTransportOptions`
confirms the first field; the native enable check above confirms the codec parameter. Each transport
update is handled, including subsequent reconfiguration. This applies to outgoing
camera video as well as shares: concurrent camera diagnostics can also consume
resources. `DPE_VIDEO_QUALITY_MEASUREMENT=1` restores upstream diagnostic requests
on the next launch.

The wrapper preserves other fields, callbacks, native method receivers, return
values and errors. It copies only the affected option objects, leaving caller
objects intact. It adds no polling, frame processing or logging. Codec selection,
bitrate limits, capture rate, encryption, congestion control, frame dropping and
quality adaptation are unchanged. Removing optional scoring avoids that work when
requested, but its effect on this user's share has not been measured.

## Capture alternatives researched

- **Portal + PipeWire + GPU processing:** preserve the existing consent and
  source-selection model. PipeWire can transport GPU buffers, so a portal does
  not inherently imply CPU copies. The [portal API][portal] grants a PipeWire
  connection to the selected streams. The current native pipeline already has
  GPU capture/encoding components; first establish whether it loses GPU-backed
  frames during scaling, preview, simulcast or encoder fallback.
- **GPU Screen Recorder:** its [upstream documentation][gsr] describes GPU-based
  recording on AMD, Intel and NVIDIA, with a portal option and vendor-specific
  encoder dependencies. Its useful principle is keeping conversion and encoding
  on the GPU. A recorder's encoded output cannot simply replace Discord's
  native source: packetization, congestion feedback, codec negotiation and
  encryption still belong to Discord. Capturing a recorder preview or routing
  it through a virtual camera may introduce copies and another encode cycle.
- **obs-vkcapture:** its [source][vkcapture-source] exchanges shared texture
  descriptors and file descriptors over a Unix socket. It avoids a compositor
  screen-capture stage for supported games, but needs a compatible consumer.
  Its [documented usage][vkcapture] enables capture when starting the game; it is
  not a drop-in Discord backend or a transparent switch for an already-running
  game. General windows and desktop sharing still require another path.
- **wl-screenrec / direct Wayland protocols:** [wl-screenrec][wl-screenrec]
  keeps transfer, conversion and VA-API encoding on the GPU and supports
  `wlr-screencopy` or `ext-image-copy-capture`. It is a useful implementation
  reference, not a replacement for Discord transport. Compositor support varies;
  lower CPU usage does not remove GPU scheduling contention.
- **DRM/KMS capture:** an alternative for complete outputs, with additional
  permission, cursor/plane and multi-GPU integration work. It would not address
  the same symptom in individual-window shares by itself. No privileged capture
  helper or system capability change is justified by the present evidence.

No external backend was installed or switched. No performance comparison of
these projects on this machine has been run.

## Native rate-control lead

Discord's [AMD encoding investigation from May 2024][amd-rate-control] describes
a feedback loop closely matching this symptom: bitrate overshoot causes WebRTC
to drop frames, the encoder adopts the lower measured frame rate, and its larger
per-frame budget causes further overshoot. Their mitigation left headroom for
frame-rate recovery. This is a **testable hypothesis**, not proof that the same
bug exists in this Linux build. The article also discusses excessive keyframe
generation when changing encoding parameters; count keyframes during rate
changes rather than assuming every drop is a capture stall.

The current native module's embedded source paths identify a `cros-codecs`
checkout at `9cdecb1`. That matches Discord's public [dependency commit][cros-commit].
Its [H.264 VA-API path][cros-h264] sends rate-control and frame-rate parameters
from the current tuning, and its [rate-control helper][cros-rate-control] converts
target/maximum bitrate to the VA-API target percentage. This supplies a much
closer implementation reference than Chromium's encoder. It does not disclose
all of Discord's private Rust adapter or prove which settings a live frame used.
Static inspection of `vaapi_media_encoding::create_tunings`, the C-to-Rust
`SessionOptions` conversion and `cc_encoder_update` confirms that this build
already adds five FPS of recovery headroom, capped at the configured target
unless the measured rate exceeds that target. The calculated tuning is
`max(current_fps, min(target_fps, current_fps + 5))`. Do not assume the 2024
mitigation is missing. Effective hardware behavior still requires testing.

## Confirmed H.264 HRD argument reversal

The pinned [H.264 source][cros-h264] computes `fullness = buffer_size * 3 / 4`
but calls `EncMiscParameterHRD::new(buffer_size, fullness)`. The
[versioned cros-libva constructor][cros-hrd] takes **fullness first, capacity
second**, matching the [VA-API structure][va-hrd]. This passes a starting fullness
larger than the buffer capacity and reduces the intended capacity. HRD/VBV
parameters control buffering in the encoder's rate controller; this is a real
configuration defect, but its contribution to the reported lag is not yet measured.

Read-only inspection confirms the same reversal in both native H.264 paths:

- AMD surface backend: file offset `0x14c2790`, load `[rax+0x38c]` into EDI,
  calculate three quarters into ESI, then call the HRD constructor.
- Vulkan surface backend: file offset `0x14c54aa`, same calculation using
  `[rbp+0x3ec]`.
- The constructor at `0x13e5ec0` stores EDI into `initial_buffer_fullness` and
  ESI into `buffer_size`. Both AV1 callers already use the correct order.
- A later bounded read of current logs identifies H.264 `kScreenshare`
  configuration, so this is relevant to the codec in use. It does not prove
  every logged rate drop was caused by HRD settings.

### Downstream Mesa/AMD handling

Read-only package and process-map inspection identifies Mesa 26.2.5, loaded as
`libgallium-26.2.5-arch3.1.so`, and a Navi 31 GPU. The versioned upstream
[Mesa VA-API handler][mesa-hrd] rejects zero capacity but otherwise copies the
application's capacity/fullness and calculates
`vbv_buf_lv = (initial_buffer_fullness << 6) / buffer_size`. It does not clamp
fullness to capacity. Discord's pinned source adds the HRD buffer after the
rate-control buffer, so the HRD settings override the earlier default capacity.

The [AMD encoder setup][mesa-vcn] carries that level and capacity into its
rate-control configuration. The [command writer][mesa-vcn-commands] emits them
to the encoder command stream. VCN 4 inherits these rate-control callbacks
through the 3.0/2.0/1.2 initializers. For an illustrative intended capacity of
4,000,000 bits, the original arguments give capacity 3,000,000 and normalized
fullness 85; the correction gives capacity 4,000,000 and normalized fullness 48
(the ratio for a full buffer is 64). This is arithmetic/source evidence, not an
observed firmware response or a benchmark. Distro-specific source changes and
firmware handling have not been independently compared.

The native `create_tunings` output also writes populated optional buffer fields;
the HRD path is conditional on that option, not an unconditional call for every
possible external configuration. Its existing FPS recovery headroom remains
unchanged. Startup and dynamic rate changes both belong in the hardware test:
the Mesa path initializes the VBV level when starting an encoder and updates
layer rate parameters when bitrate/FPS changes.

The source trace strengthens the reason to correct the arguments, but cannot
establish whether firmware rejects, tolerates or compensates for the bad values,
or how much this defect contributes to the reported degradation. No driver,
firmware, encoder or session setting was changed for this inspection.

`scripts/patch_vaapi_hrd.py` changes eight bytes across the two H.264 argument
setups. It swaps the register roles, preserving instruction lengths, call
targets, control flow, requested buffer size, codec negotiation and transport.
It accepts only explicitly reviewed input SHA-256 values after the PulseAudio
correction, or their exact already-corrected outputs, and verifies the full output
SHA-256.
Unknown modules fail compatibility preparation and require a new layout review.
The updater keeps its previous complete, approved generation on preparation
failure; a fresh installation without one reports the failure.

`patch_app_audio.patch` applies the native correction after its existing
PulseAudio correction and before publishing the wrapper and sidecars. Both
`build_local_app.py` and `client_native.py` use this shared path. Camera scope and
flip metadata are generated afterward, so their hashes identify the corrected
module. The native write uses an atomic replacement in the prepared directory,
preserving file mode and any shared input inode. Reapplication is idempotent.

`stage_updater.py` and the generated standalone Arch recipe ship the patcher and
transport adapter. The package integration
fingerprint and updater compatibility key include both, causing preparation of
new module copies instead of reuse of the old prepared cache. No Electron rebuild
is needed. Existing offline launches can retain their previous verified cache;
they do not retroactively apply the correction. The active installation and
running processes were not modified.

`scripts/stage_vaapi_hrd_fix.py` remains available for making a separate native
candidate without preparing a complete wrapper. It writes a **new directory** and
never overwrites the input or an existing output:

```sh
uv run python scripts/stage_vaapi_hrd_fix.py /path/to/reviewed/discord_voice.node \
  --output out/screenshare-hrd-candidate-new
```

Staged local candidate: `out/screenshare-hrd-candidate/discord_voice.node`;
output SHA-256
`0eecfa64b0120ca85c6454a39cf011f1f52d5661b33868ada0822580e644490e`.
The accompanying `hrd-correction.json` records the input/output hashes and scope.

The first offline package-preparation check correctly rejected the different
module pinned in `clients.lock.json` (Canary 1.0.2357). Review of that archive
confirmed identical normalized bodies for both H.264 `encode_slice` functions and
the HRD constructor. Both indirect call relocations resolve to that constructor.
The second exact layout is now supported:

- Archive SHA-256: `0dd0e78723b55d2f31b4fa6b53d4b5326d48b457260c7e9735bbebc2eb0a9a17`.
- Post-PulseAudio input: `2352578d855e5ff75c0433fe8ca0ffa6d599691a506a7908da3d0d6523cc91b8`.
- Corrected output: `62812b1287d5ea13fd762e91c18a20532734080cd3b30f0059b538281c0c4f64`.
- AMD/Vulkan edit offsets: `0x14d8680` / `0x14db39a`; constructor: `0x13fc030`.

The diagnostic enable check and frame sink also have identical normalized bodies;
the pinned transport wrapper references `videoQualityMeasurement` at `0x2ff0a4`.
`out/screenshare-pinned-layout-review.json` records the function comparisons.
Normalization was used for this manual review only: production still checks
complete file hashes, never searches for byte patterns in unknown binaries.

Seven regression tests in `tests/test_vaapi_hrd.py` cover the native edit. The executable test
runs the actual reviewed argument-setup instructions before/after correction
against changing buffer sizes, verifying both backend variants and unchanged
surrounding memory. It does not simulate a GPU. Other checks cover unknown/tampered
modules, exact edit scope, atomic failure handling, shared-source preservation,
file mode and idempotence. Wrapper tests cover diagnostic-only changes, repeated
updates, opt-in, native behavior and upgrading an existing audio wrapper. Packaging
tests cover sidecar staging and compatibility-key invalidation.

Offline preparation from the pinned archive passes in
`out/screenshare-integration-verified-20261102/report.json`. It runs the real shared
module preparation, checks the corrected hash and camera metadata, then repeats
preparation and verifies unchanged output. The prepared module and sidecars are
in that directory's `discord_voice/`; they were not loaded or installed. This
check required no Discord/Electron launch, account data, capture session or GPU.

The earlier staged module passed the isolated native VP8 delivery smoke check at
`out/screenshare-hrd-smoke/report.json`. That proves it still loads and delivers
video through an unaffected codec, **not** that H.264 driver behavior is fixed.
That historical check predates the diagnostic wrapper and was not repeated for
this integration.

## Reproducible passive analysis

The new analyzer reads a bounded snapshot of explicitly supplied log files. It
does not attach to Discord, poll it, capture pixels, collect audio or invoke a
portal. Output is constructed from numeric fields, booleans and fixed labels;
raw log lines, paths, endpoint addresses, keys and account identifiers are not
exported. Existing output files are never overwritten.

```sh
uv run python scripts/analyze_screenshare.py \
  /path/to/logs/discord-webrtc_1 /path/to/logs/discord-webrtc_0 \
  --output out/screenshare-observation.json
```

Supply rotations oldest first from one process lifetime. Each output group is a
native-thread observation, not a guaranteed unique stream: threads may be reused,
and logs can start mid-session. Unknown/mixed content groups must remain
unattributed. Absent messages do not prove zero drops or a healthy path.

## Verification tools and deferred launch validation

Validation of the passive analyzer: seven regression tests cover interleaved
camera/share attribution, sensitive-field exclusion, unknown/reused thread
classification, CPU adaptation, quality-measurement attribution and bounded
read-only file access. These checks
establish analyzer behavior, not improved viewer quality.

### Isolated sender/receiver fixture

`scripts/check_screenshare.py` and `tests/screenshare/` now exercise the copied
native voice module with a synthetic desktop, two local native connections and
a UDP relay. The default fixture uses a fresh profile, private PID/network
namespaces, Xvfb, fake audio, software VP8 and a private `/dev` without GPU/camera
devices. Hardware mode requires explicit access to one GPU render device.
It does not inherit the host display, PipeWire, PulseAudio or D-Bus endpoints.
The test runs at reduced process priority and saves counters and synthetic-only
logs, not screenshots or audio/video recordings. It reuses an existing runtime;
there is no Electron/native compilation or account login.

```sh
uv run python scripts/check_screenshare.py \
  --runtime /path/to/verified/runtime/electron \
  --voice /path/to/discord_voice.node \
  --output out/screenshare-check-new
```

Requires `bwrap`, `Xvfb`, `xvfb-run`, `nice` and an ABI-compatible runtime/module
pair. Each output directory must be new. The low-load baseline defaults to a
640x360 virtual desktop; `--source window`, `--fps 60` and `--motion static`
select additional cases. `--resolution` also accepts 1280x720 and 1920x1080;
`--bitrate` accepts 1000000, 4000000 or 6000000 bits/s. These set requested limits;
adaptation can still reduce actual output dimensions, rate or bitrate.
`--drop-video` is a negative control and **must exit
unsuccessfully**: encoding alone cannot satisfy the viewer delivery check.

The fixture checks native `kScreenshare` configuration, receiver decode and sink
delivery, plus RTCP feedback. Samples include input/encode/decode rates, drop and
adaptation counters, bitrate estimates, encoder timing, pacer delay and loss.
The native sink is acknowledged after every delivered frame, avoiding artificial
viewer drops from a stalled test consumer.

Transition scenarios require at least nine seconds. `--scenario rate-step`
lowers the requested maximum bitrate to one quarter for the middle third, then
restores it. `--scenario cadence-step` similarly lowers capture FPS to one third
and restores it. Reports record transition times, requested settings and actual
sender/receiver samples. These are controlled configuration changes, not CPU/GPU
load tests or simulations of network congestion.

`--encoder h264-vaapi --render-node /dev/dri/renderDXXX` prepares a hardware case;
replace the placeholder with the intended GPU's render node. The runner exposes
only that explicitly selected character device and rejects other device paths.
H.264 uses constrained baseline level 4.2 to accommodate 1080p60, following
[WebRTC's level limits][h264-levels]. A hardware pass requires H.264, a VA-API
encoder implementation in every encoded sample and hardware acceleration in
native screenshare logs, alongside viewer delivery. Software fallback cannot
satisfy this check. An H.264 run without a render node is permitted as a negative
control and is expected to fail.

The hardware mode remains **unvalidated on the GPU**. No render device has been
exposed during this investigation. When launch testing is authorized again, run
the original and corrected modules into separate fresh output directories with
identical `--encoder h264-vaapi --render-node ... --resolution 1920x1080 --fps 60
--bitrate 6000000 --scenario cadence-step --seconds 30` options, then repeat at
30 FPS and with the rate-step scenario. Check actual encoded dimensions and
backend before interpreting results. Capture still uses private X11; this does
not validate the Wayland/DMA-BUF path or reproduce load contention by itself.

Native setup details relevant to further work:

- Linux X11 `sourceId` is the numeric XRandR/window ID, not Electron's prefixed ID.
- Screenshare uses the quality-100 stream. A nonzero keyframe interval and
  `alwaysSendVideo` permit this synthetic sender without a Discord SFU subscription.
- Both test connections explicitly negotiate protocol 0 for synthetic frame
  encryption. Without negotiation this module can encode frames but send
  placeholder payloads. Transport still uses the private test key. These options
  are fixture-only and must not be copied into production connections.

Successful local artifacts: `out/screenshare-check-screen/report.json` (moving
monitor/30 FPS), `out/screenshare-check-window/report.json` (moving window/60 FPS)
and `out/screenshare-check-static/report.json` (static monitor/30 FPS).
`out/screenshare-check-negative/report.json` correctly fails with encoded frames
but no decoded or sink-delivered video. The first two predate the runner's added
`native_screenshare_observed` field; their native logs independently identify
`kScreenshare`. The reused Electron SHA-256 is
`4223184105a4ee9f162bbfefabbe3a4f0eccc239400d38f88fa56390dd3d945d`;
the voice-module hash is recorded above and in each fixture's `inputs.json`.

Additional software-only runs at 640x360/30 FPS:

- `out/screenshare-cadence-step/report.json` passes delivery and records the
  native input and receiver returning to the requested cadence after restoration.
- `out/screenshare-rate-step/report.json` passes delivery and records the bitrate
  target changing and recovering after restoration. The estimate can differ
  from the requested cap; inspect transport overhead and sample timing too.
- `out/screenshare-h264-no-device/report.json` correctly fails with no encoded or
  decoded video when the candidate requests H.264 without GPU access. Repeating
  with level 4.2 in `out/screenshare-h264-no-device-level42/report.json` also fails
  as expected. Neither run validates hardware negotiation or encoding.

Five fixture-validation unit tests reject missing/mixed screenshare evidence,
wrong codecs, software fallback (including a later fallback), delivery failure
and unintended device paths. Every fixture report explicitly leaves
`performance_improvement_verified` false: setup/delivery success alone cannot
establish smoothness or prove a performance improvement.

Integration validation on 2 November: `bun run lint` and `bun run test` pass
(567 Python and 277 JavaScript tests). Logs are retained at
`out/screenshare-integration-lint.log` and `out/screenshare-integration-tests.log`.
Checks ran sequentially at nice 19 on one CPU core. No end-to-end launch, host
GPU access or deliberate CPU/GPU load test was run for this integration. The
offline archive-preparation report is linked above. Local links and instructions
were reviewed; `git diff --check` passes.

These checks establish preparation and fixture behavior, **not** improved viewer
performance, a 1080p result, SFU/DAVE equivalence, or Wayland/VA-API behavior under load.
GPU contention and real viewer validation remain outstanding. The parser also
now recognizes the actual `EncoderUsageResource` log spelling; a positive
regression test prevents silently missing successful CPU adaptations.

### Deferred performance validation

1. Validate original versus HRD-corrected H.264 using the native VA-API encoder,
   first at low load, then 1080p30/60 and recovery from controlled load. Keep
   deliberate hardware stress deferred while the user's Discord session needs
   the GPU. Extend the working receiver fixture to private Wayland capture too.
2. Measure capture inputs, encoded frames/bytes, encoder time, bitrate target,
   packet pacing, RTT/loss and receiver decode cadence together. Compare static
   and high-motion synthetic content at 1080p30/60 with fixed network conditions.
   Keep the CPU/GPU stress phase deferred while the user needs the current call.
3. Compare optional scoring enabled/disabled separately from the HRD correction.
   Check rate-control overshoot and frame ownership/conversion; separately check
   scheduling delays and network-estimate reductions. A backend replacement is
   warranted only if capture is shown to be the bottleneck. The implemented
   correction does not establish that every observed slowdown shares one cause.
4. Validate the integrated changes against the original, including recovery from load,
   resize, source switch, cancellation and encoder fallback. A final real viewer
   check remains necessary and must not be started unattended.

[sender]: https://webrtc.googlesource.com/src/+/refs/heads/main/video/video_stream_encoder.cc
[dropper]: https://webrtc.googlesource.com/src/+/refs/heads/main/modules/video_coding/utility/frame_dropper.cc
[portal]: https://github.com/flatpak/xdg-desktop-portal/blob/main/data/org.freedesktop.portal.ScreenCast.xml
[gsr]: https://git.dec05eba.com/gpu-screen-recorder/about/
[vkcapture-source]: https://raw.githubusercontent.com/nowrep/obs-vkcapture/master/src/capture.c
[vkcapture]: https://github.com/nowrep/obs-vkcapture
[wl-screenrec]: https://github.com/rosalyntg/wl-screenrec
[amd-rate-control]: https://discord.com/blog/from-blocky-to-brilliant-improving-video-quality-on-discord-go-live-on-amd-gpus
[cros-commit]: https://github.com/discord/cros-codecs/commit/9cdecb1
[cros-h264]: https://raw.githubusercontent.com/discord/cros-codecs/9cdecb1/src/encoder/stateless/h264/vaapi.rs
[cros-rate-control]: https://raw.githubusercontent.com/discord/cros-codecs/9cdecb1/src/backend/vaapi/encoder.rs
[cros-hrd]: https://raw.githubusercontent.com/chromeos/cros-libva/v0.0.13/lib/src/buffer/enc_misc.rs
[va-hrd]: https://intel.github.io/libva/structVAEncMiscParameterHRD.html
[h264-levels]: https://webrtc.googlesource.com/src/+/refs/heads/main/api/video_codecs/h264_profile_level_id.cc
[mesa-hrd]: https://gitlab.freedesktop.org/mesa/mesa/-/blob/mesa-26.2.5/src/gallium/frontends/va/picture_h264_enc.c
[mesa-vcn]: https://gitlab.freedesktop.org/mesa/mesa/-/blob/mesa-26.2.5/src/gallium/drivers/radeonsi/mm/radeon_vcn_enc.c
[mesa-vcn-commands]: https://gitlab.freedesktop.org/mesa/mesa/-/blob/mesa-26.2.5/src/gallium/drivers/radeonsi/mm/radeon_vcn_enc_1_2.c
