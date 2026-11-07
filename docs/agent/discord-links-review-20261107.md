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
with 3 skips, and 93 Rust passes with 6 ignored tests. The packaging regressions
also check the generated desktop MIME declaration.

This establishes UI, IPC and bootstrap dispatch behavior. It does not establish
a browser-to-signed-in Discord flow on every desktop, or portable desktop
installation. No account login, messages, calls or physical camera tests run.

## CI compatibility repairs

The first integration run exposed two failures inherited from `dev`'s native
helper migration:

- Ubuntu 24.04's PipeWire 1.0.5 lacks the optional SPA `DROP` preference flag and
  newer stream functions. Initial preferences now leave original offers intact
  when the installed headers cannot express that fallback. Explicit rate caps
  remain available. The unused lazy query is removed; the unused-buffer return
  API is resolved dynamically. Older libraries retain unused tokens in a bounded
  pool until reuse or removal, without publishing empty or stale frames. The
  regression checks single checkout, duplicate returns, retirement and capacity
  recovery. The [older filter](https://github.com/PipeWire/pipewire/blob/1.0.5/spa/include/spa/pod/filter.h)
  and [stream API](https://github.com/PipeWire/pipewire/blob/1.0.5/src/pipewire/stream.h)
  define the compatibility boundary.
- Arch packaging lacked the new helper build dependencies and attempted to write
  native caches beneath its read-only source mount. Its image now installs the
  repository's pinned Rust toolchain and helper dependencies, including Git for
  applying the pinned game hook patch. The workflow mounts only generated `out/`
  writable alongside its package output; source remains read-only. Both callers
  build the image from the repository root so it can read `rust-toolchain.toml`.

The fixes pass lint and the full suite above. A private Ubuntu 24.04 container
compiles against its actual PipeWire 1.0.5 headers and passes all 16 PipeWire Rust
tests, three SPA negotiation tests and both initial-preference/producer-lifetime
C fixtures. Evidence: `out/pr-11-review/ubuntu-compat.log` and
`out/pr-11-review/ubuntu-upstream.log`.

The repaired Arch image builds the native helpers and passes the package smoke
check with read-only source, a writable cache and the pinned prebuilt UI. The
report checks the camera library dependencies and generated package. Evidence:
`out/pr-11-review/arch-smoke-verified.log` and
`out/pr-11-review/arch-packages-verified/packaging-report.json`. This check uses a
dummy Electron runtime and creates a non-publishable package; it does not qualify
a release runtime.
