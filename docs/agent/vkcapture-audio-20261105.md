# Vkcapture automatic application audio, 5 November 2026

## Cause and fix

The game broker already authenticated hook connections with Unix `SO_PEERCRED`,
but kept the PID private to its peer record. Only the capture request ID, method
and source type reached the audio helper. Optimized window captures therefore
always opened the manual audio picker: they have no PipeWire video node to
correlate with a compositor window.

The broker records the same-user peer PID and `/proc/PID/stat` start time at
connection, revalidates them at capture start, and includes that identity in the
start reply. Both bundled and stock OBS hooks use this path. The controller
passes it to the renderer; only the active native capture exposes it to audio.
Discovery lists and picker labels still omit process identities.

The audio helper uses this identity for its existing application-and-descendants
route when automatic application matching is enabled. It validates UID, liveness
and start time; an exited or reused PID cannot become a new automatic target.
Missing identity retains manual selection. Disabled autodetection, explicit
manual choices and re-enabling audio retain their existing behavior. Capture
shutdown removes the route. Silence still waits for playback.

## Validation

- `bun run lint`: passed.
- `bun run test`: 736 Python tests passed; 443 JavaScript tests passed, 3 skipped.
- Broker regression checks the selected identity against the real socket peer.
- Controller and renderer regressions cover identity propagation and active-only
  snapshots. Audio regressions cover node-free automatic selection, disabled
  detection, forced manual selection, missing/reused processes and shutdown.

These checks use synthetic processes and mocked audio graphs. No live Discord
call, physical media capture or desktop recording was performed. The source
changes have not been staged into a runnable capture bundle in this check.
