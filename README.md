# Discord patched Electron

Work in progress: preserve Discord's native desktop streaming on newer Electron,
with working Krisp and application-specific screen-share audio. Package names use
`discord-<discord-channel>-<electron-channel>-patched-electron-bin`:
Discord Stable/PTB/Canary × prerelease Electron (three packages). Stable and
nightly Electron targets are disabled.

**Wayland is the primary desktop target; X11 is additional support.** The current
audio adapter is enabled by default on Wayland and supports native Wayland capture with a private PipeWire
mix, including PulseAudio applications and multiple audio processes. niri has
automatic window matching; other compositors get an audio-app picker. See
[Wayland audio](docs/wayland-audio.md) for usage, tests and remaining live-call
and custom-runtime qualification.

A separately compiled [camera probe fix](docs/camera-startup.md) reduces native
webcam startup time without rebuilding Electron. [Runner controls](docs/ci-setup.md#changing-local-limits-while-the-build-runs) explain live CPU/RAM limits.

Published binaries will require **x86-64-v3** (AVX2, including Haswell/first Ryzen;
no AVX-512 requirement). See [CPU targeting and build reuse](docs/build-caching.md)
for incremental Ninja builds, compiler caching and completed-artifact reuse.

The implementation follows [the plan](docs/plan.md). Packaging is based on the
pinned current `discord_arch_electron` AUR recipe, not a replacement client.

## Current status

- A local **Discord Canary 1.0.2357 / Electron 46.0.0-alpha.1** test build now
  opens on native Wayland. Launch instructions and measured results are in the
  [handoff](docs/handoff.md). The runtime was compiled on the PC for x86-64-v3;
  no Blacksmith compiler was used for this build.
- Imported the existing package, launcher and Krisp patcher with exact provenance.
- Locked current Stable, PTB and Canary core/module distributions independently.
- Implemented atomic source locks, strict channel validation, content fingerprints,
  the existing resource/bootstrap fixes and validated Krisp patch application.
- Hourly [release discovery](docs/discovery.md) retains a durable queue on
  `ci-state`, including every new prerelease and separate Discord updates.
  Identical polls create no commit. The automatic compile/release/AUR coordinator
  remains unfinished; no AUR publishing key is configured.
- Imported Discord's complete 44-branch delta and verified that applying it to its
  original upstream base reconstructs Discord's exact source tree.
- The prerelease source and dependency ports compile successfully with pinned,
  additive patches. Historical stable/nightly ports are retained but disabled.
  See [source ports](docs/source-ports.md).
- The compiled prerelease passes native video delivery/teardown, capture lifetime
  through garbage collection, and offline Krisp suppression. Native CI passes
  for Stable, PTB and Canary. Local checks also verify automatic Wayland selection,
  hardware WebGL/VA-API decoding and physical webcam startup (8.37 to 2.33 seconds
  with the helper). See [runtime evidence](docs/evidence/prerelease-runtime-local.json).
  All ten custom-runtime X11/Wayland audio fixtures also pass, including both
  PulseAudio setups and manual app selection. Real Discord calls and GNOME/KDE
  desktop qualification remain untested.
- A shared generator produces all three binary package recipes. Arch packaging
  checks use real client/modules and a dummy runtime; see [packaging](docs/packaging.md).
- Compilation uses the containerized PC runner. A bounded Blacksmith experiment
  remains available in code but is not used for the unattended build. Public
  release and AUR publication remain disabled. Compiler artifacts and the local
  test app are available; there is no published binary release yet.

## Local checks

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-test.txt
npm ci --ignore-scripts
.venv/bin/python -m unittest discover -s tests -v
python3 scripts/lock_clients.py
```

The client-lock command accesses Discord's manifests; tests use pinned local data.
Re-running the lock command without changed distributions leaves the file untouched.

To reproduce source assembly without downloading Chromium:

```sh
python3 scripts/assemble.py patch-base \
  --destination .work/patch-base --report .work/patch-base.json
python3 scripts/assemble.py prerelease \
  --destination .work/prerelease --report .work/prerelease.json
```

Use a fresh destination each time. The current prerelease port assembles;
later conflicts still fail explicitly and preserve diagnostics. Textual assembly
does not qualify a runtime for publication. `reference` assembles the exact Discord fork for the
initial capacity build; it is not the requested latest-Electron product.

See [CI setup](docs/ci-setup.md), [build status](docs/status.md) and
[upstream attribution](THIRD_PARTY.md).
