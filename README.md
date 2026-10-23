# Discord patched Electron

Work in progress: preserve Discord's native desktop streaming on newer Electron,
with working Krisp and application-specific screen-share audio. Package names use
`discord-<discord-channel>-<electron-channel>-patched-electron-bin`:
Discord Stable/PTB/Canary × Electron stable/prerelease/nightly (nine packages).

**Wayland is the primary desktop target; X11 is additional support.** The current
opt-in audio correction has passed X11 fixtures only. Wayland selection and
audio isolation are required before calling the application-audio feature ready.
See the [audio investigation](docs/audio-investigation.md).

Published binaries will require **x86-64-v3** (AVX2, including Haswell/first Ryzen;
no AVX-512 requirement). See [CPU targeting and build reuse](docs/build-caching.md)
for incremental Ninja builds, compiler caching and completed-artifact reuse.

The implementation follows [the plan](docs/plan.md). Packaging is based on the
pinned current `discord_arch_electron` AUR recipe, not a replacement client.

## Current status

- Imported the existing package, launcher and Krisp patcher with exact provenance.
- Locked current Stable, PTB and Canary core/module distributions independently.
- Implemented atomic source locks, strict channel validation, content fingerprints,
  the existing resource/bootstrap fixes and validated Krisp patch application.
- Hourly [release discovery](docs/discovery.md) retains a durable queue on
  `ci-state`, including stable maintenance releases and separate Discord updates.
  Identical polls create no commit; automatic compile/publication dispatch is
  still gated on the unfinished runtime and audio qualification.
- Imported Discord's complete 44-branch delta and verified that applying it to its
  original upstream base reconstructs Discord's exact source tree.
- Stable, prerelease and nightly source candidates now assemble with separate,
  pinned compatibility patches. Their Chromium/dependency ports and compilation
  are **not yet validated**. See [source ports](docs/source-ports.md).
- A full custom runtime has **not been compiled**. Successful Krisp binary patching
  is **not** a claim that noise suppression has been tested on the new runtime.
- Native video delivery/teardown and offline Krisp suppression pass on all three
  bundled-runtime baselines. The reusable [runtime checks](docs/runtime-validation.md)
  still need to pass on the custom builds, alongside real call/audio checks.
- A shared generator produces all nine binary package recipes. Arch packaging
  checks use real client/modules and a dummy runtime; see [packaging](docs/packaging.md).
- The reference build can use the containerized PC runner or a single bounded
  Blacksmith experiment within the newly unlocked account's free allowance.
  Recurring compilation and AUR publication remain disabled pending source
  compatibility and runtime validation. No binary releases exist yet.

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
python3 scripts/assemble.py stable \
  --destination .work/stable --report .work/stable.json
```

Use a fresh destination each time. The current three candidate ports assemble;
later conflicts still fail explicitly and preserve diagnostics. Textual assembly
does not qualify a runtime for publication. `reference` assembles the exact Discord fork for the
initial capacity build; it is not the requested latest-Electron product.

See [CI setup](docs/ci-setup.md), [build status](docs/status.md) and
[upstream attribution](THIRD_PARTY.md).
