# Discord patched Electron

Work in progress: preserve Discord's native desktop streaming on newer Electron,
with working Krisp and application-specific screen-share audio. Package names use
`discord-<discord-channel>-<electron-channel>-patched-electron-bin`:
Discord Stable/PTB/Canary × Electron stable/prerelease/nightly (nine packages).

The implementation follows [the plan](docs/plan.md). Packaging is based on the
pinned current `discord_arch_electron` AUR recipe, not a replacement client.

## Current status

- Imported the existing package, launcher and Krisp patcher with exact provenance.
- Locked current Stable, PTB and Canary core/module distributions independently.
- Implemented atomic source locks, strict channel validation, content fingerprints,
  the existing resource/bootstrap fixes and validated Krisp patch application.
- Imported Discord's complete 44-branch delta and verified that applying it to its
  original upstream base reconstructs Discord's exact source tree.
- The current upstream stable, prerelease and nightly source ports are **not yet
  complete**. Source-application diagnostics are in [docs/evidence](docs/evidence).
- A full custom runtime has **not been compiled**. Successful Krisp binary patching
  is **not** a claim that noise suppression has been tested on the new runtime.
- Full compilation and AUR publication remain disabled pending free-runner setup,
  source compatibility and runtime validation. No binary releases exist yet.

## Local checks

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
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

Use a fresh destination each time. The stable command currently fails explicitly
where the port is required. It does not discard conflicting changes or publish
a partially patched runtime. `reference` assembles the exact Discord fork for the
initial capacity build; it is not the requested latest-Electron product.

See [CI setup](docs/ci-setup.md), [build status](docs/status.md) and
[upstream attribution](THIRD_PARTY.md).
