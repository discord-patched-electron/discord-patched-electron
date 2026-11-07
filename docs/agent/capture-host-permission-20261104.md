# Direct capture from restricted Electron processes

The manual codec profiles ran with `NoNewPrivs: 1`, despite their terminal parent
having `NoNewPrivs: 0`. Children inherit that restriction, so neither the explicit
permission installer nor the KMS producer could use pkexec. Changing profiles or
ignoring the permission check cannot repair this.

`integration/updater/capture-host.cjs` probes an unprivileged transient systemd
user service. Only when it actually reports `NoNewPrivs: 0` can restricted
processes use that route. The existing explicit polkit installer and fixed,
root-owned KMS server remain responsible for authorization. Status probes do not
prompt or capture. Missing tools, an unavailable manager, or a restricted user
manager preserve the blocked result. Chromium sandbox settings remain intact.

Only KMS producers use the service route; other capture workers retain their
existing launch. Each producer owns a unique service, receives the desktop
session variables and literal arguments through pipes, and stops on stdin EOF
or explicit unit stop. No preload variables are forwarded from Electron.

Validation: mock permission and service tests cover allow/revoke, missing or
restricted managers, trusted executable checks, literal arguments and cleanup.
A disposable Node process launched with `setpriv --no-new-privs` successfully
queried the existing grant and ran a benign Python pipe worker with
`NoNewPrivs: 0`; the worker exited on stdin EOF. No root installer or desktop
capture was started during that check.

The manual app was also missing its entire capture bundle. Stage it with
`scripts/stage_capture_backends.py`; the regular local builder already does this.

Final checks: `bun run lint` passed; `bun run test` passed 696 Python tests
and 414 JavaScript tests (two skipped). The deployed manual app reports both
`allowed: true` and `available: true` from a restricted disposable process.
