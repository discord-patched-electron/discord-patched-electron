# Discord link review, 7 November 2026

[PR #11](https://github.com/discord-patched-electron/discord-patched-electron/pull/11)
adds desktop MIME declarations, an onboarding opt-in and bare Discord URL
delivery. Review targets `dev`, preserving the Rust, capture and settings work.

## Findings and fixes

- The original persisted switch could turn off without relinquishing the OS
  association. Settings now offers an explicit registration action and reads
  actual default status. Changing the handler happens in system settings;
  opening DPE never reclaims it. Electron's
  [unregister API](https://www.electronjs.org/docs/latest/api/app/#appremoveasdefaultprotocolclientprotocol-path-args)
  is limited to macOS and Windows. Native packages declare a candidate through
  `MimeType`; the [MIME default selection rules](https://specifications.freedesktop.org/mime-apps/latest/default)
  remain the desktop's authority. Portable copies need a working installed desktop entry.
- Registration failures previously left an enabled saved preference, were
  retried twice in Welcome, then swallowed. The explicit action verifies the
  resulting OS state. Welcome displays failures and supports retry or opting out;
  finishing is disabled until its initial status query completes.
- A single pending URL could overwrite cold/early requests. A bounded queue now
  preserves up to 16 validated Discord URLs until the desktop core is ready.
  Other protocols, controls, malformed and oversized values are discarded.
- Welcome's second-instance path previously lost link arguments. It queues them
  for relaunch. The launcher handoff retains pending URLs and strips delivered
  URLs, preventing an update/restart from reopening an old invite or channel.
- Three merge conflicts were resolved while retaining webcam preferences,
  capture controls and existing settings IPC restrictions. New tests cover the
  actual transformed bootstrap, queue lifecycle and the trusted registration IPC.

## Validation

The transform applies and compiles against the retained pinned OpenAsar source.
The real settings harness uses the verified Electron 46.0.0-alpha.1 runtime in
an isolated Xvfb display with a fresh profile. It checks registration failure and
success, focus, 150% zoom in a narrow window, an externally changed handler,
Welcome failure and opt-out. Protocol APIs and `xdg-mime` are private stand-ins;
no real user association changes. Images and reports are retained under
`out/pr-11-review/settings-links-final/`.

An additional probe uses real Electron registration, `xdg-mime` and `gio open`
with two owned desktop entries, a private XDG configuration and a fresh D-Bus
session. It changes the private default from the other fixture to DPE's fixture
and confirms that the synthetic URI reaches its handler. Evidence is under
`out/pr-11-review/os-protocol-gio/`. Bare generic `xdg-open` in Xvfb has no desktop
browser backend; that case is not counted as a dispatch pass.

Repository lint and the full suite pass: 800 Python tests, 468 JavaScript passes
with 3 skips, and 92 Rust passes with 6 ignored tests. The packaging regressions
also check the generated desktop MIME declaration.

This establishes UI, IPC and bootstrap dispatch behavior. It does not establish
a browser-to-signed-in Discord flow on every desktop, or portable desktop
installation. No account login, messages, calls or physical camera tests run.
