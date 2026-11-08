# Desktop media controls (5 November 2026)

Discord's Chromium media sessions publish calling/notification sounds and other
media through Linux MPRIS. DPE now defaults `openasar.desktop.mediaControls` to
false, including for existing profiles without the key. General → Media exposes
the shared settings switch; enabling it restores upstream desktop media controls.
Changes apply next launch, with the existing restart action and notice.

`entry.cjs` applies `HardwareMediaKeyHandling` and `MediaSessionService` feature
disables before Electron readiness in both launcher stages. Unrelated flags stay
intact; these two features are removed from enable-features while the preference
is off. Opting in leaves explicit user launch flags untouched. No renderer media
API, playback controls, audio output, or notification delivery is changed.
The retained Chromium source confirms the media-session feature gates audio-focus
publication in `content/browser/media/session/audio_focus_delegate_default.cc`.

## Validation

- `bun run lint` and `bun run test` pass (Python suite plus 425 JavaScript
  passes, three skips).

- Regression tests cover normalization, flag preservation, conflicting enables,
  idempotence, opt-in behavior and restart tracking in both directions.
- Real settings harness, scoped with `DPE_SETTINGS_SCOPE=media-controls`, passes
  keyboard activation, retained focus, autosave, tab remounts, restart tracking,
  narrow windows, 125% zoom and a long label. Report:
  `out/desktop-media-settings-20261105-focused/settings.json`.
- Fresh Electron profiles under private D-Bus sessions and Xvfb play an eight-second
  synthetic WAV on a loop. With the default, playback advances and no MPRIS name
  appears; with opt-in, playback advances and
  `org.mpris.MediaPlayer2.chromium.instance…` registers. Evidence and probe:
  `out/desktop-media-native-20261105/`.
- Reused `/mnt/storage/dpe-middle-click-20261101/artifact/runtime/electron`
  (46.0.0-alpha.1); no compilation, account login, message, call or camera capture.
- The unscoped harness stops at its existing CSS-editor growth assertion before
  reaching the media test, both on the host display and isolated X11. The focused
  scope runs the same settings window, preload, IPC and shared switch.

This verifies native publication and local settings behavior, not a live DM call
or exact visual parity with signed-in Discord.
