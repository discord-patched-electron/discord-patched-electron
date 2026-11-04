# H.264 bitrate cap after reconfiguration, 2026-11-03

The user reported that resolution retention improved but outgoing bitrate roughly
halved and motion looked heavily compressed after the
[quantizer contract correction](encoder-allocation-20261103.md).

Current native logs confirm an unintended consequence of that correction. Two
H.264 streams requested a 9 Mbps maximum, then reconfigured to an effective
4 Mbps maximum and target. Their minimum changed from 500 kbps to 30 kbps.
The transport continued reporting 9 Mbps available, 9 Mbps receiver REMB, zero
receiver loss and roughly 5 ms RTT. Frame-drop messages repeatedly used a
4 Mbps target. These are distinct from an encoder merely producing fewer bytes
for an easy scene.

Sanitized evidence, containing numeric measurements rather than account IDs:
`out/quality-regression/bitrate-cap-regression/live-sanitized.json`.
The analyzer now records effective post-policy stream limits separately from
requested encoder configuration limits. Multiple layers remain individual
samples, never a summed bitrate or a guessed connection identity.

## Mechanism and missed test coverage

[WebRTC's ReconfigureEncoder](https://github.com/webrtc-mirror/webrtc/blob/main/video/video_stream_encoder.cc)
selects a default resolution/bitrate table when `is_qp_trusted` is false and no
encoder-specific table exists. It clamps the stream maximum and rewrites its
minimum and target from that table. In this native version, the 1080p default
maximum is 4 Mbps. That table is not the negotiated transport allowance.

The earlier synthetic healthy-bandwidth test initialized at 9 Mbps before the
AMD encoder metadata arrived. It did not exercise the later reconfiguration
which applies the cap. Resolution retention and successful frame delivery were
insufficient regression gates. A moving-scene test must trigger reconfiguration
after encoder initialization and inspect both effective limits and actual
encoded bitrate.

The repair must preserve the quantizer correction, genuine congestion scaling,
the application's bitrate envelope and real encoder-advertised limits. It must
not globally increase the negotiated ceiling or pretend padding bytes improve
picture quality. Exact 1920×1080 framing remains required.

## Implemented correction and validation

The helper intercepts only ReconfigureEncoder's default untrusted-QP limit
lookup. It verifies the current owner is a screenshare using H.264, obtains
that encoder's current metadata and requires the tracked AMD VBAQ contract with
no explicitly advertised hardware bitrate limits. It then leaves this local
default-limit result absent, preserving the application's stream envelope.
The separate bandwidth scaler's limits and real allocation inputs are unchanged.
Other backends, camera streams and explicit hardware limits retain native behavior.
The complete module hash, callsite, owner register and native object layouts are
validated. Independent source/disassembly review found no actionable blockers.

The bounded moving-scene control/fixed pair reapplies identical transport options
2.5 seconds after initialization. Both use the existing quantizer/allocation
fixes; only the new envelope correction differs. Control reproduces the live
9-to-4 Mbps cap and 186 rate-limiter drops, with reported output around
3.63–5.15 Mbps and 24–36 FPS after reconfiguration. Fixed retains the requested
9 Mbps maximum, reports approximately 9.55–10.15 Mbps RTP media and 60–61 FPS,
and records one startup rate-limiter drop. Reported RTP media is not an exact
payload budget measurement and can exceed the configured encoder cap over short
intervals. Both runs passed native sender/receiver validation at exact 1080p.

Artifacts: `out/quality-regression/bitrate-cap-regression/{control,fixed}/report.json`.
The tracked fixture now accepts `--reconfigure-after` so subsequent validation
can cover this live trigger. No account calls or live client restart were used.

Corrected helper SHA256:
`cf9256e8038de6a4f8c3929ec3a8f024662f720ad0555674eca0fd3f252ce0e7`.
This fixes the demonstrated cap regression; a live viewer-quality comparison
remains unverified. It does not raise Discord's negotiated bitrate ceiling.
