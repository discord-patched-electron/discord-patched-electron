# Implementation status

Updated 23 October 2026. This is a bootstrap implementation, not a working binary release.

Wayland is the primary target, as clarified by the maintainer. X11 fixture success
does not satisfy the application-audio release requirement. The current desktop
reports niri; its portal path is the first live qualification target.

## Verified

- Release discovery reads paginated official feeds, resolves nightly source tags,
  detects moved tags and preserves missed stable/maintenance releases. The durable
  state branch uses content-based no-ops and fast-forward writes without merges.
  Local tests cover racing writers, nightly coalescing and independent Discord
  updates. See [discovery](discovery.md); compiler dispatch and AUR publication
  are not yet connected to the queue. The first two CI polls passed and the
  unchanged second poll left the remote state commit untouched. Discovered PTB
  1.0.224 and Canary 1.0.2357 passed separate CI native/Krisp baseline and
  six-recipe checks; the bootstrap locks now include those exact snapshots.
  See [client-update evidence](evidence/client-updates-ci.json).
- The vendored AUR files match commit `3b290dffd5eb840faed445bdc2bdb18572839471`.
- Current manifests lock Stable 1.0.166, PTB 1.0.224 and Canary 1.0.2357 separately.
- The existing Krisp patcher applies to each channel's real native binary. The
  validation wrapper confirms that only the expected six-byte conditional branch
  changes and that repeating the patch is a no-op. Input/output hashes are in
  `evidence/*-krisp.json`; all explicitly say runtime validation is still false.
- Existing AUR resource/bootstrap changes apply to the current Stable app.asar.
- The shared generator produces all nine recipes, with fixed binary checksums
  and separate installation/profile paths. Real Stable, PTB and Canary payloads
  pass the generated makepkg preparation and packaging steps in a clean Arch
  container with an explicitly nonfunctional runtime fixture. This is packaging
  evidence only; see [packaging checks](packaging.md). The updated nine-package
  checks also pass with native shared-library validation, including the narrowly
  documented dispatch exception; see [CI evidence](evidence/packaging-native-dependencies-ci.json).
- The imported Discord patch exactly reconstructs the fork tree from its base.
- Unit tests cover source integrity, cross-channel isolation, fingerprints,
  byte-preserving failure paths, patch idempotency and Arch prerelease ordering.
- The PC runner is online and has fetched Chromium and installed dependencies.
  Its first attempt stopped on an optional Sentry CLI download; the corrected
  workflow resumes the existing checkout. See [capacity measurements](build-capacity.md).
- Blacksmith's first reference attempt completed fetching and hooks, then failed
  at the depot_tools Python launcher before C++ compilation. Explicit tool
  bootstrap fixes the launcher locally without moving the pinned tool revision.
- The corrected local reference run `40005436647` has reached C++ compilation.
  It remains in progress; no completed custom runtime exists yet.
- All three bundled runtimes pass synthetic native frame delivery and teardown,
  module loading, and an offline Krisp off/on/off comparison (53.6 dB reduction
  in the noise-only interval, retained speech). See [runtime checks](runtime-validation.md).
  This does not establish custom-runtime, live-call or microphone behavior.
  The same three-channel checks pass on standard GitHub runners; see
  [CI evidence](evidence/native-ci.json). Successful compiler jobs now trigger
  automatic validation of their reference artifact without another compile.
- Current 45, 46 and 47 source candidates assemble reproducibly using the imported
  delta plus separate explicit compatibility patches. All skipped imported file
  changes have replacement patches or documented upstream equivalents.
- Static inspection of all three voice modules finds soundshare PID/loopback
  options and a native PulseAudio stream-monitoring path. The module
  adapter retains that path and adds a private PipeWire mix for Wayland. Exact
  niri cast/window matching, multiple audio processes, native PipeWire and
  PulseAudio playback, source switching, cancellation, and crash cleanup now
  have real native Wayland fixtures. Other compositors have an explicit audio
  selector. See [Wayland audio](wayland-audio.md); custom-runtime and live-call/
  microphone qualification remain open. The Wayland adapter is now enabled by
  default; `DPE_APP_AUDIO=0` restores upstream behavior.
- Candidate builds now select x86-64-v3 through a project-owned GN toolchain.
  Real compiler fixtures verify cache hits, header/CPU invalidation and reuse
  across checkout paths/server restarts. Incremental reference objects remain
  intact; verified completed archives skip the compiler. See [CPU and caching
  checks](build-caching.md). Candidate CI is wired for each Electron channel,
  followed by automatic native/Krisp checks against all three Discord clients.

## Current local verification

- A physical Logitech UVC webcam with the installed Canary 1.0.2360 module and
  its bundled Electron 44.9.3 reached the first 1280×720 frame in 1.74 seconds
  with the camera fix, versus 6.05–7.78 seconds upstream (two alternating pairs).
  No camera images were saved and no microphone or call was used.
  See [camera startup](camera-startup.md).
- Wayland is default-enabled. Local native capture tests passed without the old
  opt-in variable on PipeWire-Pulse (operator-driven selector) and real PulseAudio
  plus PipeWire (automatic niri identity). All nine phases and crash cleanup passed.
  See [retained local evidence](evidence/camera-wayland-local.json).
- Ordinary voice/webcam connections keep unrelated transport options unchanged.
  The camera helper has fake-driver tests for exact caller scope, supported formats,
  unavailable enumeration, changed capabilities and explicit disablement.

## Previous full CI audio validation

[Checks 40030377851](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/40030377851)
passed at `d6ad1ab`: 58 Python tests, ten JavaScript tests, source assembly,
compiler-cache checks, all nine package recipes, native video/offline Krisp on
all three bundled clients, three X11 audio fixtures and seven Wayland fixtures.
Wayland covers Stable/PTB/Canary with PipeWire-Pulse and real PulseAudio, plus
explicit audio selection/cancellation with automatic compositor discovery off.
All cases verify native PipeWire video, selective audio, Discord exclusion,
source/audio lifecycle, unchanged default devices and helper SIGKILL cleanup.
See [retained reports](evidence/wayland-audio-ci.json).

The earlier mixed-server PTB dropout was fixed by keeping Pulse streams on their
server's clock. The final checks use a stricter 90% clean-frame requirement.
The completed test compositor is checksummed and cached; these checks use
standard GitHub runners and do not allocate another Electron compiler.
They still explicitly leave custom-runtime, real-call and microphone-call
qualification false. No binary release is advertised on this evidence.

## Outstanding before a release

1. Finish the bounded reference capacity build and inspect its diagnostics.
   Blacksmith has unlocked the account and the maintainer confirms 168 of the
   3,000 allowance minutes used. The first hosted experiment failed before compilation;
   use the installed PC runner's persistent cache to qualify the launcher fix.
2. Confirm cold-build disk/memory use and successful incremental resumption.
3. Finish the Discord integration ports to Electron 45, 46 and 47: their source
   candidates assemble, but Chromium/dependency patches and C++ still need builds.
4. Prove native streaming and Krisp behavior on the custom runtime.
5. Validate the generated packages with real custom runtimes, then finish the
   compiler coordinator, release ledger and automatic AUR publisher. Discovery
   and the durable backlog are implemented; no publishable runtime exists yet.
6. Qualify the completed Wayland audio adapter with the real client's UI and a
   custom runtime, including a call with microphone/Krisp active. These are release
   checks; Wayland routing is enabled by default. Test actual GNOME/KDE desktops and sandboxed
   applications separately; automated identity is currently niri-specific.

The standard GitHub Actions workflow runs checks. The local reference workflow
uses four CPUs, 12 GiB RAM, up to 8 GiB additional swap and two compiler tasks.
The separate Blacksmith experiment uses 16 CPUs, 64 GB RAM and 12 compiler tasks
within the reported free allowance. Recurring Blacksmith compilation is disabled;
no AUR key is configured and no public binary is advertised.

## Probe incident

The bundled Discord executable ignored `ELECTRON_RUN_AS_NODE` during one local
probe and launched its normal bootstrap/profile instead. It was stopped. Future
runtime probes must set an isolated `DISCORD_USER_DATA_DIR`, disable downloads
where appropriate and never assume the Electron RunAsNode fuse is enabled.
