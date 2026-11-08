# Runtime update and managed startup splash, 8 November 2026

## Changes

Runtime bootstrap first tries the exact cached pins. If an application upgrade
changes those pins, bootstrap can copy a verified runtime from another application
cache for the startup UI. The copy receives its own resource link; running
applications retain theirs. The normal splash worker then downloads and prepares
the new runtime before launching Discord. Exact-runtime qualification mode keeps
its existing pin requirement. Signed release and manual approval rules remain.

The dev manager previously prepared Discord modules before starting Electron.
It now stages a private hook in the managed application's `check.py`. The normal
startup worker prepares modules and editable helper links, publishes the profile
installation, and shares immutable runtime files while the splash is visible.
Archive locks and the catalog lock protect shared storage; the manager retains
version/profile locks and owns process-group cancellation and restart.
Installed application payloads do not receive this development hook. Restart the
manager to use the new launch path; existing managed versions need no rebuild
for this hook.

## Verification

`bun run lint` and `git diff --check` passed. Tests and desktop checks were not
run, as requested. End-to-end verification is
left to the user. No Electron compilation, account interaction or release approval
was performed.
