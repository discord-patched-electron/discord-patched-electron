# Higher screenshare bitrate request, 2026-11-03

The user requested a higher outgoing bitrate after fixing the unintended 4 Mbps
cap. The normal frontend ceiling is 9 Mbps for eligible high-quality shares,
with a separate 10 Mbps call-wide ceiling. This experiment requests 16 Mbps in
the test candidate; it does not prove Discord's live SFU will grant that request.

`DPE_SCREENSHARE_MAX_BITRATE` opts into a requested ceiling in bits per second.
The frontend adapter accepts integers above 9 Mbps through 32 Mbps, otherwise
stays inactive. Normal packaged launches remain unchanged without the variable.
The test launch selects 16 Mbps and permits an explicit `0` to disable it.

The adapter wraps Discord's existing maximum-bitrate callback for owned outgoing
Go Live connections only. It preserves lower explicit limits and changes only
the reviewed high-quality H.264/AV1 default or explicit 9 Mbps result. Null
callbacks normally select the same default, so that case is guarded by the
current method shape and verified constants. Unknown upstream shapes stay native.
The requested video envelope continues through the existing quality manager,
native transport configuration and Video event used for opcode 12 signaling.
The default call-wide ceiling is raised with the video request, retaining its
existing 1 Mbps margin. Starting/minimum rates and congestion controls are not
inflated. Cameras and received streams are unchanged.

## Native capacity check

The retained corrected helper was reused without an Electron or helper rebuild.
A private synthetic moving-scene test requested 16 Mbps, with the private
fixture's separate call-wide ceiling also raised to 16 Mbps. Identical transport
options were reapplied after 2.5 seconds to exercise the earlier cap regression.
The final six-second interval delivered 12.88 Mbps of non-padding RTP payload
at 60.11 encoded FPS, exact 1920×1080, with zero rate-limiter drops. Native
receiver validation passed. The existing overshoot controller reduced the
encoder allocation; it was not bypassed to force the output to equal 16 Mbps.

Evidence: `out/quality-regression/bitrate-16m/summary.json` and `report.json`.
This verifies local encoding capacity, not higher allocation from a live server.
No logged-in calls, account actions or running client restart were performed.
