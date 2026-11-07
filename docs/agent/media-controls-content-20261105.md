# Content-only desktop media controls (5 November 2026)

The optional desktop-media setting must expose videos, uploaded audio/voice
messages and embedded players without exposing Discord's UI sounds.

The Electron 46 follow-up `patches/ports/46/media-content-only.patch` adds a
Chromium dependency patch. It filters audio-only HTTPS resources in Discord's
application `/assets/` and `/sounds/` namespaces, with exact Discord application
and static-CDN hosts. Attachments, blob URLs, third-party players and videos
retain media sessions. This is a source-based policy, not a duration cutoff.
Other sound sources introduced by upstream or mods need separate review.

Both initial playback and subsequent metadata updates filter only the browser
media-session delegate's audio flag. The renderer's metadata and audio pipeline
retain their actual audio tracks: notification volume, output routing, looping,
background playback and delivery are unchanged. Real media is reported to the
delegate as persistent, including voice messages under Chromium's five-second
transient threshold. The default-off startup flags and restart requirement remain.

The new dependency patch is appended to the locked port, with a new checksum
and assembled tree. Native runtime fingerprints include it automatically. This
changes the native runtime identity and requires an Electron rebuild; existing
verified runtimes cannot demonstrate or provide this filtering. Port status is
reset to `source-only` until compilation and runtime qualification are complete.

## Evidence

- Fresh assembly reproduces the locked Electron tree:
  `out/media-controls-policy/assembly-final.json`.
- The dependency patch applies to a private copy of the retained Chromium files.
- `tests/test_media_session_policy.py` compiles and executes the shipped policy
  against notification/ring sources, uploads, videos, embeds, blob sources and
  lookalike hosts; it checks both delegate integration sites.
- The changed `web_media_player_impl.cc` passes a syntax-only compile with the
  retained build's pinned Clang, generated headers and actual compiler flags:
  `out/media-controls-policy/syntax.log`. No retained source or object changed.
- The real settings harness passes the focused `media-controls` scope, including
  keyboard saving, retained focus, restart state, narrow windows and zoom:
  `out/media-controls-settings-final-20261105/settings.json`. It uses an older
  verified runtime and therefore proves settings behavior, not the native filter.
- `bun run lint` and `bun run test` pass: 741 Python tests; 443 JavaScript passes
  and three skips.
- Full compilation and native MPRIS playback tests with the changed runtime are
  pending. No signed-in Discord, messages, calls or camera tests were performed.
