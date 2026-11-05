# Actual niri transition timing, 4 November 2026

Follow-up to the [receiver-clock investigation](receiver-clock-av1-padding-20261104.md).
The user reports that black-page-to-Minecraft window animations judder remotely
while the sender's self-preview remains smooth. This check reproduces actual
niri focus animations rather than substituting texture changes in one window.

## Setup

The existing paired test profiles used the staged candidate runtime and corrected
native module. Both were muted during measurements. A generated black SDL window
and the existing Minecraft window occupied the captured DP-2 output. No browser
content was navigated, chat message sent, or desktop/call video saved. The source
was optimized KMS DMA-BUF capture at 1920×1080/60 FPS on a 240 Hz output. AV1X
encoded the preserved source inside 1920×1088. The receiver was visible on a
separate 60 Hz output; the sender's self-preview was visible on a 75 Hz output.

Two 26-second runs alternated focus six times, three seconds apart. Numeric
artifacts are in `out/niri-transition-20261104/`. Capture counters were sampled
every 10 ms, native transport/codec counters every 100 ms, and both players used
`requestVideoFrameCallback` alongside receiver `requestAnimationFrame`. Times
were aligned with `Date.now()`. These are timing diagnostics, not panel scanout
measurements. Counter polling bounds capture timing but cannot establish each
individual frame's timestamp; a delayed poll can coalesce several frames.

The first run accidentally requested a receive-only native stats mask for the
sender. It still measured its packet queue and self-preview correctly, but its
missing outbound fields must not be interpreted as zero frame drops. The second
run used the correct sender mask. `summary.json` describes this second run.

## Observations

These maxima use the interval from 500 ms before to two seconds after each focus
request. Queue delay is a native pacer statistic, not an end-to-end latency value.

| Transition | Maximum reported pacer delay | Longest viewer frame-callback gap |
| ---------- | ---------------------------- | --------------------------------- |
| Minecraft  | 456 ms                       | 100.1 ms                          |
| Black      | 125 ms                       | 83.3 ms                           |
| Minecraft  | 240 ms                       | 83.4 ms                           |
| Black      | 212 ms                       | 116.6 ms                          |
| Minecraft  | 213 ms                       | 83.3 ms                           |
| Black      | 118 ms                       | 83.3 ms                           |

Across the trace, capture counters advanced 1,559 frames with no return errors.
Sender and receiver native stats both advanced 1,554 encoded/decoded frames;
their polling intervals do not start at exactly the capture counter boundaries.
There were no reported bitrate-limiter, encoder or network-frame drops in the
second run. The self-preview's longest callback interval was 26.8 ms, consistent
with presenting 60 FPS content on its 75 Hz output. Receiver rAF intervals stayed
at or below 16.8 ms and native maximum decode time was 2–3 ms in the measured
transition windows. The first run also reproduced receiver gaps while capture
and self-preview remained regular.

## Initial interpretation and limit

For this reproduction, capture throughput, decoder throughput and the viewer's
UI event loop do not explain the repeated remote gaps. Frames largely reach the
decoder, but presentation is uneven. Packet queue growth is observable before
the final receiver presentation stage. This strengthens the delivery/pacing
diagnosis and weakens the earlier suggestion that bitrate-limiter drops alone
explain this specific window-switch symptom.

The trace does not isolate encoder burst sizes, network/SFU packet delivery and
receiver playout response from each other. It does not prove that the receiver's
playout policy is optimal, or that every capture backend is free of timing gaps.
No codec, bitrate, congestion-control or receive-buffer setting was changed on
this evidence. In particular, a larger fixed receive buffer might hide jitter
by adding latency; that is not a verified fix. The remaining work includes the
receiver presentation path as well as encoder and sender pacing. This initial bounded
check did not establish a fix or Windows parity.

## Fix and validation, 5 November

The follow-up isolates the large gaps to sender packet queuing. During six
transitions, capture and GPU completion counters continued at approximately
60 FPS (largest gap between observed completion advances: 21 ms). The receiver's
animation loop stayed regular and maximum reported decoding time was 2–3 ms.
A cloned incoming track, consumed with `MediaStreamTrackProcessor` and closed
immediately without pixel readback, already had delivery gaps before player
presentation. Sender primary RTP queue delays rose at the same transitions.
The second outbound substream was RTX, not a second simulcast encoder.

A temporary numeric-only native probe confirmed that the active share pacer ran
at 9 Mbps on a 9 Mbps allocation, with no congestion-window blocking observed.
Tracing `SetPacingFactor` identified the **screenshare ALR policy**, which
explicitly overrides the general pacer defaults. Changing the general defaults
had not reached this path. The native policy is the same
[upstream WebRTC default](https://webrtc.googlesource.com/src/+/refs/heads/main/rtc_base/experiments/alr_experiment.cc):
`1.0,2875,80,40,-60,3`. Its first two fields are the pacing multiplier and queue
budget in milliseconds. This is evidence of a stock upstream policy, not a
measurement of vanilla Discord or Windows; explicit experiments can override it.

The retained change is `2.5,0050,80,40,-60,3`: regular WebRTC burst headroom and a
50 ms queue budget. It leaves the ALR thresholds/group, codec allocation, share
ceiling, padding rate and congestion feedback intact. As documented by the
[WebRTC pacer](https://webrtc.googlesource.com/src/+/refs/heads/main/modules/pacing/g3doc/index.md),
the queue budget is a target for draining the average queue, not a guarantee
that each packet or end-to-end latency stays below 50 ms. Instantaneous packet
sending can exceed the encoder allocation while a burst drains; this does not
raise the encoder's average bitrate limit.

`scripts/patch_video_pacing.py` changes six ASCII bytes in the unique default
string in each of the two fully reviewed native modules. Whole-file hashes,
exact offset/content checks, atomic replacement and compatibility aliases bind
it to those modules. Explicit upstream field trials still take precedence.
The unsuccessful general-pacer instruction changes and experimental encoder
frame-size caps were removed. No AMD-specific restriction is imposed on NVIDIA,
Intel, other codecs or their encoders.

The receiver correction also now supplies Chromium's planned `reference_time`
and marks the source as remote, enabling its existing presentation smoother.
The corrected microsecond clock and hardware-frame wrapper remain necessary.
A generated native regression sends 45 frames in three-frame bursts with
regular future deadlines: the old receiver collapses them to 15 callbacks; the
complete correction delivered 39 in the final fixture. This creates no fixed
receive delay and copies no pixels. Receiver scheduling alone did not solve the
actual niri transitions; correcting the active sender policy was necessary.

### Live comparison

The paired profiles used the same KMS 240 Hz source, 60 FPS AV1X share and visible
60 Hz receiver. The final repeat runs used the ordinary helper with **no added
encoder frame-size cap**. Only timing/counter data were saved. These runs used
Discord's native `updateFieldTrial` API with the exact retained policy, then the
same default was prepared in the native module. No debug hook remains in the
staged app or launchers.

| 26-second run               | Viewer callbacks | 99th-percentile callback gap | Longest gap | Gaps over 45 ms |
| --------------------------- | ---------------: | ---------------------------: | ----------: | --------------: |
| Original active 1.0× policy |             1473 |                      66.6 ms |    299.9 ms |              17 |
| 2.5×, 100 ms, final helper  |             1546 |                      16.8 ms |     50.1 ms |               3 |
| 2.5×, 50 ms, first repeat   |             1547 |                      16.8 ms |     33.4 ms |               0 |
| 2.5×, 50 ms, second repeat  |             1549 |                      16.8 ms |     33.4 ms |               0 |

The bounded acceptance criteria were: six actual window switches per run,
99th-percentile callback gap at most 20 ms, longest gap at most 35 ms, and zero
gaps above 45 ms on the 60 Hz viewer. Both 50 ms runs passed. Occasional two-tick
intervals remain; this is improved observed pacing, not a claim of perfect
presentation or Windows parity. Run-to-run scene/network variation also matters.
The original-policy run included the subsequently removed frame cap, so it is
not a strict single-variable final-helper A/B. The intermediate API comparisons
and the queue observations establish the policy effect separately.

Artifacts are `actual-policy-control-*`, `final-av1-60-*`,
`screen-policy-50-first-*` and `screen-policy-50-no-cap-*` under
`out/niri-transition-20261104/`. Generated AV1, HEVC and H.264 GPU/native/Electron
round trips passed with the uncapped helper. Physical NVIDIA/Intel hardware,
remote WAN conditions, other FPS targets and live H.264/HEVC viewing were not
measured in this final comparison. Their common pacing code is preserved except
for this screenshare default; compatibility is not a substitute for those tests.

Final verification of the prepared 50 ms native default passed all three
1920-wide, 60 FPS GPU/native/Electron round trips (`final50-native-av1-vaapi`,
`final50-native-h265-vaapi`, `final50-native-h264-vaapi`). Both reviewed native
module revisions passed scope regeneration; repeating the complete module
preparation left every native patch unchanged. `bun run lint` and `bun run test`
passed: 702 Python tests and 424 JavaScript tests, with three JavaScript skips.
The ordinary candidate runtime now contains the verified receiver scheduling
changes, and its preload is the uncapped padded helper (SHA-256
`3d282a46b232395b6d8942f49ae22cb3e36f9a7149e3bc6564325fe897c0e321`).

The experimental entry points are
`out/receiver-investigation-20261104/candidate/sender.sh` and
`out/receiver-investigation-20261104/candidate/viewer-padded.sh`. They select the
updated default runtime and prepared modules, keeping their existing separate
profiles. Test calls/apps were stopped, original profile module links restored,
and original desktop focus restored when available. No hibernation was requested
for this follow-up. Temporary numeric probes were removed from the staged app.
