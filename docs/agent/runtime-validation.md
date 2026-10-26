# Native runtime checks

Use these fixtures to check native video, capture lifetime and offline Krisp
against a bundled baseline or verified custom runtime. Start with reproduction
below; [earlier baseline results](#baseline-measured-on-23-october-2026) are retained
for comparison.

The fixture copies the runtime and gives it a private profile, HOME, XDG paths,
display and unavailable audio/DBus endpoints. It does not open the normal Discord
bootstrap or use an account. Synthetic results do not establish live-call quality.

## Reproduction

Run `uv sync --locked` and `bun install --frozen-lockfile --ignore-scripts`. Install a C++ compiler,
Node development headers at `/usr/include/node`, Xvfb and `xauth`. Use an extracted
distribution and staged, patched modules from the same locked Discord channel:

```sh
uv run python scripts/check_krisp_audio.py generate out/audio-fixture
uv run python scripts/probe_runtime.py /path/to/runtime \
  --executable electron --modules /path/to/staged/modules \
  --audio-fixtures out/audio-fixture/input --output out/runtime-test
uv run python scripts/check_krisp_audio.py analyze \
  out/audio-fixture out/runtime-test --report out/runtime-test/audio-report.json
```

For the active Canary bundled baseline, use `DiscordCanary` as the
executable name. Every probe requires a fresh output directory. It copies inputs,
uses a bounded subprocess group, and retains reports and logs on failure. The
speech fixture was generated with eSpeak NG; its phrase, generator and checksum
are recorded in `tests/audio/speech.json`. No recorded user speech is used.

Run the same fixture for each custom runtime and channel combination before
publication. Additional acceptance remains necessary: starting/joining/stopping
real Discord streams, microphone noise suppression, hardware/GPU paths, X11 and
Wayland capture, and two-application screen-share audio isolation. The reports
explicitly leave those claims false and are not publication authorization.

`scripts/check_runtime_clients.py --output <fresh-directory>` downloads the locked
core/voice/Krisp payloads, verifies their channel identity, patches Krisp and runs
both checks for Canary. Pass `--runtime <extracted-custom-runtime>` to
reuse a compiled runtime. Stable and PTB are disabled. The `native-baselines` job
in the Checks workflow runs the bundled baseline on standard GitHub runners,
without spending Blacksmith compiler minutes. Failures retain diagnostics.
Ubuntu's CI image requires an AppArmor user-namespace exception for these copied
executables. The workflow scopes that exception to its private test paths using
[Chromium's documented profile approach](https://chromium.googlesource.com/chromium/src/+/main/docs/security/apparmor-userns-restrictions.md),
and keeps Chromium's sandbox enabled.
The first successful sandbox launch also identified `libpulse.so.0` as a required
voice-module dependency. The package now declares `libpulse` as required, rather
than relying on a system Electron package to bring it in. Clean Arch packaging
checks inspect the native modules for unresolved shared libraries.
There is one explicit exception: the unchanged `discord_dispatch.node` shipped in
all three locks needs `libssl.so.1.1` and `libcrypto.so.1.1`. [Nixpkgs' merged fix](https://github.com/NixOS/nixpkgs/pull/517309)
documents this module as effectively unused and omits that legacy dependency.
The packaging check records these missing libraries and permits them only for
the inspected module SHA-256, never for voice/Krisp or a changed dispatch binary.
This preserves the existing package's module contents; it does not establish
working dispatch functionality on a machine without OpenSSL 1.1.

## Release workflow

The shared `Native runtime validation` workflow is called by release qualification
after a successful candidate compiler run. It downloads the artifact, verifies
its checksum against the completed compiler report, and checks Canary's modules.
A manual `reference_run` input can retest a retained artifact without compilation.
Only successful main-branch compiler runs qualify. Reports record the compiler
and validation commits separately. Earlier three-channel results are retained in
[prerelease evidence](../evidence/prerelease-runtime-local.json); current release
qualification is Canary-only. Publication and approval are described in the
[release guide](releases.md).

The separate [Wayland audio fixture](wayland-audio.md) covers actual native
portal video and selective application audio, including multiple processes and
both Pulse servers. Its bundled-runtime result does not replace the custom-runtime
and real-call checks above.

## Frame-capture lifetime

`scripts/check_frame_capture.py RUNTIME --provenance ARTIFACT_PROVENANCE --output
FRESH_DIRECTORY` exercises Discord's main-process capture API with two synthetic
local windows. It checks wrapper creation, started/stopped events, constraint
changes, repeated stops, immediate stop, and target/consumer destruction after
forced garbage collection. Active captures must remain alive until stopped;
each stopped event must arrive exactly once. The test denies device permissions
and external requests and saves no frame pixels.

The fixture passes against the compiled reference on native Wayland. Native CI
runs it against downloaded compiler artifacts under Xvfb, alongside the separate
native-sink video delivery and offline Krisp checks. A passing lifecycle test
alone does not establish native-sink delivery or a working Discord call.

## Baseline measured on 23 October 2026

| Discord payload | Bundled Electron | Native frame and teardown | Offline Krisp noise reduction |
| --------------- | ---------------- | ------------------------- | ----------------------------- |
| Stable 1.0.166  | 42.11.15         | Pass                      | 53.6 dB                       |
| PTB 1.0.223     | 44.9.3           | Pass                      | 53.6 dB                       |
| Canary 1.0.2354 | 44.9.3           | Pass                      | 53.6 dB                       |

The input modules are the real channel-specific modules with the existing AUR
Krisp patch applied. Evidence is in `evidence/*-runtime-baseline.json`, including
executable/module hashes, runtime versions, fixture hashes and measurements.
**These are bundled-runtime baselines, not compiled custom-runtime results.**

The same checks passed for all three channels in GitHub Actions run
[`40010461967`](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/40010461967),
at `3933811`, on Ubuntu 24.04. [CI evidence](../evidence/native-ci.json) records each
runtime's input hashes, native frame result, fixture identity and audio checks.

The native helper compiles against the pinned Discord public frame header and
resolves `DeliverDiscordFrame` from the runtime. A real renderer calls
`electron.discord.install('createDiscordStream')` through its preload, creates a
MediaStream, and receives a synthetic 64×48 I420 red frame. Canvas readback measured
RGBA `[255, 1, 1, 255]`. All 11 frames were released, including the checks for a
missing stream and a stopped stream. The fixture also loads each channel's native
voice module and enumerates four Krisp models.

The optional audio test initializes the native voice engine with fake capture
devices and calls its offline `processBatchAudioFiles` API. It processes the same
synthetic speech/noise file with Krisp **off, on, then off again**, while disabling
ordinary noise suppression, gain control and echo cancellation. Each pass
processed one file with zero failures, and the processing error callback stayed
empty. Native logs confirmed Krisp initialization.

The analyzer permits up to 200 ms of processing latency. It requires at least
15 dB reduction in a noise-only interval, speech correlation of at least 0.6 with
the clean reference and an improvement of at least 0.03, retention of at least
half the disabled speech gain, and restoration of the disabled behavior. Negative
tests demonstrate that unchanged noisy output and fully muted output fail. This
is a reproducible regression signal, not a human intelligibility assessment.
