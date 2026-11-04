# Optimized source loss and monitor audio

Main-process helper exit already sent `dpe-capture-ended`, but the renderer
ignored active selections. The renderer now publishes the exact request ID to
the transport adapter, which clears the native desktop source and invokes
Discord’s existing ended callback once. Stale IDs cannot end replacement shares.
The bridge does not directly free an active native capture descriptor.

A game surface STOP now allows five seconds for a replacement texture before
ending capture. This preserves resize/recreation while bounding a permanently
lost surface. Socket EOF already ends game capture; Wayland stopped and missing
KMS connector paths already terminate their producers. Camera removal remains
native-owned, with existing notification-filter regression coverage.

The native snapshot exposes the active request ID to the audio helper. Confirmed
optimized monitors can honor skip-audio-picker without requiring a PipeWire video
node. Lost requests retire their audio routes. Games and explicit manual selection
retain their picker behavior.

Validation uses synthetic source-end, stale/duplicate event, audio-routing and
camera-notification fixtures. The game helpers compiled with warnings as errors
in `out/source-disconnect/game`. Physical unplugging and logged-in Discord calls
were not tested. Settings checks are in
`/mnt/storage/dpe-capture-disconnect-20261102/settings/settings.json`; the codec
icon uses the danger token and one shared icon/text gap, measured in the harness.
Combined checks are logged under `out/picker-refinement/disconnect-*.log`.
