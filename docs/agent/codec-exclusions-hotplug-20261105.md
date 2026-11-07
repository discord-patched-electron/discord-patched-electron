# Codec exclusions and device hotplug, 5 November 2026

## Codec options

Removed VP9 from the exclusion menu, preference validation and page adapter's
accepted exclusion names. The reviewed native module has no VP9 encoder.
Incoming decoder capabilities remain unchanged.

Legacy saved VP9 exclusions are discarded. A profile that excluded the four
real codec choices while leaving only VP9 allowed now resets to no exclusions,
rather than leaving the user unable to share. The UI and save validation prevent
excluding all four remaining choices. Regression tests cover these cases and
rejection of new VP9 exclusion requests.

The user guide no longer presents VP8 or VP9 as quality improvements. A short
note explains VP8's existing CPU encoding and why VP9 has no exclusion option.

## Hotplug review and checks

No additional production hotplug defect was reproduced. Existing guards were
reviewed and exercised, rather than hiding device notifications or swallowing
native errors:

- The real Wayland client/server fixture now adds/removes unrelated outputs,
  removes the selected output with a frame pending, delivers a late frame and
  reannounces the same monitor name. Capture stops exactly once with `ENODEV`,
  does not switch monitors or revive the old session, and can start again in a
  fresh session. Three cycles run under ASan/UBSan.
- The main-process capture controller fixture repeats unplug/explicit reselection
  three times for both direct Wayland and KMS. Duplicate terminal events and late
  readiness cannot revive a dead node or leave listeners behind.
- The V4L2 preload fixture exercises 50 unplug/replug cycles, including removal
  during format enumeration and between enumeration and format probing. A new
  camera reuses the descriptor with different capabilities. `ENODEV` reaches the
  native caller, and replug works without stale cached capabilities.
- Camera mirroring accepts failed conversion with no destination image without
  touching null planes. The synthetic conversion fixture passes ASan/UBSan.
- Device-notification tests repeat 50 camera and display-audio plug/unplug cycles
  using mutable native arrays and reused device objects. Real changes remain
  visible to Discord, including changed device indices.
- The previously fixed camera-hotplug abort came from Pulse microphone-volume
  reconfiguration. Both prepared dev voice modules still contain the reviewed
  invalid-channel/volume guard. Eight Pulse patch tests pass, including executing
  the guard and checking that failure unlocks the existing audio lock. The
  speaker-volume method reads a live sample specification with a null check;
  it does not use the microphone method's problematic cached channel field.

## Evidence and limits

Artifacts: `out/hotplug-codecs-20261105/`. The actual screensharing settings
harness passes, including absent VP9, the last-codec guard, persistence, keyboard
and responsive states. It uses Electron 46.0.0-alpha.1, SHA-256
`4223184105a4ee9f162bbfefabbe3a4f0eccc239400d38f88fa56390dd3d945d`.
The first harness attempt encountered the dev runtime's relative resources
symlink; the successful run uses a private standalone copy with its own fixture
resources. No installed runtime or running profile was modified.

`native-hotplug-guards.json` records the prepared voice module hashes and guard
inspection. Camera and monitor tests use synthetic drivers/protocols; no physical
camera or monitor was unplugged, no account call was made, and no camera or
desktop images were recorded. These checks establish the exercised removal,
replug and cleanup contracts, not every physical driver or GPU hot-unplug case.
Full lint and suite results are retained in `lint.log` and `tests.log`.
