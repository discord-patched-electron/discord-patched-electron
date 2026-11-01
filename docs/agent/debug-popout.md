# Blank Discord voice debug popout

The 1 November 2026 report had `window.opener === null`, a complete document and
an empty `#app-mount`. Discord's public `popout.454185a14bae34ee.js` only notifies
the main window of its loaded state through `window.opener.postMessage`. Without
that relationship, the parent never renders into the popout. The console's
unload permissions-policy warning does not explain the missing loaded handshake.

Discord's desktop core explicitly gives its main window `sandbox:false`, but
omits `sandbox` from `DEFAULT_POPOUT_OPTIONS`. Electron 46 defaults that child to
`sandbox:true` and severs the opener when the process-level sandbox settings
differ. This is documented in Electron's
[window-opening guide](https://www.electronjs.org/docs/latest/api/window-open).

The in-memory desktop transform in `integration/updater/patches.cjs` sets
`sandbox:false` in those popout defaults to match the existing main renderer.
Discord's same-origin `/popout` allowlist, Node-disabled page context and context
isolation remain intact. There is no global sandbox switch or change to external
navigation. The exact replacement must occur once; missing or duplicate sites
reject an unsupported client before activation. Downloaded ASARs stay unchanged.

## Validation

An account-free HTTP fixture on retained Electron 46.0.0-alpha.1 reproduced the
failure: the default sandbox produced a null opener and empty root. Matching the
sandbox restored the loaded message and parent-side rendering. Local evidence:
`out/popout-investigation/probe.log`.

The same fixture passed without the fix on stock Canary 1.0.2422's bundled
Electron 44.10.1. Its runtime was extracted from Discord's checksum-verified
host archive and ran only the synthetic fixture with a fresh profile. Evidence:
`out/validation-storage/stock-popout-comparison/probe.log`. This identifies a DPE
compatibility issue with the newer runtime, not a reproduced stock Discord bug.

`scripts/check_desktop_launch.py` now opens Discord's actual `/popout` from a
fresh login page, waits for its real loaded event, checks its opener and origin,
renders a synthetic marker from the parent, and confirms page Node globals are
absent. It closes and reopens the same named window to cover registry cleanup.
These assertions also run during release launch/relaunch qualification.

Canary 1.0.2357 and the testing release's 1.0.2422 snapshot both passed on first
launch and relaunch, with two successful popout opens per launch. Reports are
under `out/validation-storage/debug-popout-fixed-check/` and
`out/validation-storage/debug-popout-current-check/`. The latter uses the exact
client snapshot from `config/update-proposals/canary-prerelease.json`.

Lint and 504 Python plus 198 JavaScript tests passed. No account login or voice
call was automated; displaying live voice debug statistics still requires a
manual call check. `out/launch-test.sh` points to the fixed 1.0.2422 local build
and retains its existing manual-test profile default.
