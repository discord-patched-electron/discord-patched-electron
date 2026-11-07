# Discovery relaunch failure and CSP investigation, 5 November 2026

## Release failure

[Discovery run 41936340989](https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/41936340989)
failed in Canary's package qualification on the second launch of 1.0.2444.
The initial login and popout checks passed. The relaunch console recorded
`ReferenceError: injectedModuleUpdater is not defined` in `getModulePath`.
The later successful discovery poll did not qualify this failed candidate.

The verified desktop-core archive in its `release-checks-canary` artifact's
client lock no longer declares the legacy module-updater binding in the native
module IPC implementation. Our in-memory transform still referenced that name.
Optional chaining cannot protect an undeclared identifier.

`patches.cjs` now resolves the legacy fallback through the bootstrap module
explicitly supplied in `entry.cjs`'s require cache. Native updater lookup and
unknown-module null results retain their existing behavior. The regression test
executes the transformed function without any legacy binding, covering both
updater branches and an unknown module.

## CSP screenshot

The supplied screenshot blocks `sentry-ipc://start/sentry_key` under `connect-src`.
This is Sentry's Electron renderer handshake, not a Discord API or module download.
The verified 1.0.2444 `mainScreenPreload.js` initializes its bundled Sentry SDK and
falls back to protocol-based fetch when no preload IPC interface is exposed.
OpenAsar stubs main-process crash reporter setup; that stub does not stop the
preload's separate initialization. Its page-level No Track code runs later.

No CSP allowance or telemetry enablement was added. The screenshot establishes
this blocked handshake; it does not establish that every reported CSP violation
has the same cause. This is separate from the main-process exception that failed
release qualification.

The subsequent [complete compatibility port](client-2444-compatibility.md)
also connects DPE to Discord's remaining native-updater API and ports the
working tree's native adapters.

## Validation

Diagnostics and isolated fixture results are under `out/discovery-csp-fix/`.
The launch fixture uses the failed release's exact client lock and release source
commit `5c5548c3437666860d05801f93e10f10518edb14`, with the corrected transform,
and a retained compiled runtime. It does not use account credentials.
This isolates the release failure from unrelated native-patch work already in
the working tree. Login, popout and same-profile relaunch checks passed, with
the login form present and no uncaught exception on both launches. Full working-tree lint and
unit suites passed (708 Python tests; 427 JavaScript passes, 3 skips).
