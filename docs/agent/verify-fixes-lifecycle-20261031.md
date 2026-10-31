# Independent lifecycle snapshot review — 2026-10-31

One reproducible, low-priority regression remains in the camera preload change.
The flags repair and updater lifecycle changes passed the supplied suites and
additional independent checks. No production fixes were made.

## Candidate and boundaries

- Prepared checkout: `/home/he3als/.t3/worktrees/discord-patched-electron/t3-d7a742f3`.
- Confirmed HEAD: `f90b251caee6b4e7d265c0e450e53a70d97b0aba` (initially clean).
- Candidate: `fixes-review-snapshot.zip`, 43 entries, SHA256
  `8f1d8e60afa105bf2b67771f0e6c3479aa70d5e21ac0b6c7139035529f03d166`.
  Verified the ZIP hash, entry list and every entry against the adjacent manifest
  before overlaying it once. The baseline launcher comparison reads `git show HEAD:…`
  in this prepared checkout, without modifying the overlaid launcher.
- Read `CONTRIBUTING.md`, the agent index, current launcher user documentation,
  CI check selection, and current implementation comments. Historical reviewer
  reports were not used as evidence.
- All tests ran with `unshare --user --map-current-user --net env UV_OFFLINE=1`;
  Python used `uv run`. Dependencies came from `uv sync --locked --offline` and
  the authorized shared `node_modules` symlink.
- The original dirty repository was neither read nor modified. No network,
  services, real installations, accounts, real profiles, device capture, paid CI,
  or runtime compilation were used. Native work was limited to existing tiny
  fake-camera driver/helper fixtures. Full-suite signing tests use disposable keys.

## Finding: P3 — a semicolon in a bundled app path now disables the camera helper

Location: `integration/launcher/discord-launcher.sh:108-111`, specifically the
new bundled-root `LD_LIBRARY_PATH` entry at line 110.

Trigger: run the bundled launcher from a valid Linux directory such as
`/tmp/<fixture>/apps;games`, with camera fast probing enabled (the default).
Previously the full helper pathname in `LD_PRELOAD` loaded correctly: semicolons
are not separators in `LD_PRELOAD`. The candidate instead searches for the helper
by basename through `LD_LIBRARY_PATH`, where semicolons **are** separators. Shell
quoting cannot change the dynamic loader's interpretation of that variable.

Deterministic evidence: `repro_camera_paths.py` runs the actual launcher templates
against the repository's fake V4L2 driver and compiled probe fixture. Under the
semicolon path the baseline returns 0 with empty stderr. The candidate cannot
load `libdpe-camera-probe.so`, then the fixture assertion proving probe filtering
fails (exit `-6`). Its stderr includes:

```text
ERROR: ld.so: object 'libdpe-camera-probe.so' from LD_PRELOAD cannot be preloaded (cannot open shared object file): ignored.
```

Impact: camera fast probing is absent for this path even though it worked on the
baseline. The fixture's assertion abort is a test failure, not evidence that
Discord itself crashes. Actual camera startup latency was not measured. The
trigger is unusual, hence P3. Normal paths pass on both versions, and the supplied
space-path fix is independently confirmed: baseline fails, candidate passes.

The current launcher user guide does not restrict installation paths this way.
The codec preload function explicitly rejects `;` for its separate loader-search
use, but that restriction neither validates the camera directory nor prevents
this previously working bundled path.

Suggested coordinator action: preserve direct-path loading where the path is safe
for `LD_PRELOAD`, or resolve through a helper-only directory whose path is safe for
loader search. Do not attempt to fix this by adding shell quotes to the environment
variable. No implementation change was applied during this review.

## Validation

| Check                                              | Result                                      | Log                               |
| -------------------------------------------------- | ------------------------------------------- | --------------------------------- |
| `test_launch_flags.py`                             | 40 passed                                   | `flags.log`                       |
| `test_launcher.py`                                 | 10 passed                                   | `launcher.log`                    |
| `test_camera.py`                                   | 4 passed                                    | `camera.log`                      |
| `test_runtime_bootstrap.py`                        | 10 passed                                   | `bootstrap.log`                   |
| `tests/updater/runner.test.cjs`                    | 12 passed, including nested cases           | `runner.log`                      |
| Full `bun run test`                                | 368 Python + 86 JavaScript passed; no skips | `full-test.log`                   |
| `bun run lint`                                     | Passed, including final report/index        | `lint.log`                        |
| `bash -n integration/launcher/discord-launcher.sh` | Passed                                      | `shell-syntax.log`                |
| Independent runner boundaries                      | 7 passed                                    | `independent-runner.log`          |
| Independent flags and group boundaries             | 3 passed                                    | `independent-flags-bootstrap.log` |
| Independent baseline/candidate camera matrix       | 2 passed, 1 regression failure              | `camera-paths.log`                |

Independent runner cases cover exactly 65,536-character records, 1-byte UTF-8
fragmentation, coalesced records, oversize accumulation across chunks, cancellation
inside a progress callback with invalid output remaining in the same chunk, and
an unterminated record after a valid result. Limits are measured in JavaScript
string units, consistent with the existing implementation, rather than wire bytes.

Independent flags cases cover an invalid byte appended to an otherwise managed
file, escaped comments for every recovered line, exact-byte backups, preservation
of mode `0640`, successful repaired launch flags, and revision protection against
an intervening external edit. The additional native cleanup fixture verifies that
a TERM-ignoring descendant of a reaped helper is stopped while an unrelated
process session remains alive.

The supplied tests additionally exercise success/failure/cancellation cleanup of
private-output descendants, native cancellation when the helper completes between
polls, existing user arguments and profile selection, quick/offline/cache paths,
and the actual fake-camera preload boundary. Current packaging code places the
bundled camera helper at the app root and general runtime libraries in `runtime/`;
no evidence supported a separate general-library-shadowing finding.

## Reproduction and artifacts

Run from the prepared checkout root:

```sh
unshare --user --map-current-user --net env UV_OFFLINE=1 \
  uv run python out/verify-fixes-lifecycle/repro_camera_paths.py
unshare --user --map-current-user --net env UV_OFFLINE=1 \
  uv run python out/verify-fixes-lifecycle/independent_flags_bootstrap.py
unshare --user --map-current-user --net env UV_OFFLINE=1 \
  node --test out/verify-fixes-lifecycle/independent_runner.test.cjs
```

The first command intentionally fails one assertion on the immutable candidate.
Fixtures and logs are retained under `out/verify-fixes-lifecycle/`. A copy of this
report, all logs, fixtures and verification metadata is supplied to the coordinator
under:

```text
/home/he3als/.t3/worktrees/discord-patched-electron/review-offline-reliability-20261030/out/project-reliability/verify-fixes-lifecycle/
```

Coverage limits: synthetic process/driver fixtures cannot establish real Electron
startup, GTK interaction, hardware-camera behavior, calls, or distribution packaging
compatibility. No actual Arch package build or installation was attempted. Processes
that intentionally create a new session can escape process-group signals; no such
current helper path was demonstrated, so that is not a finding. PID-reuse races were
not reproduced and are not reported. The explicitly excluded interrupted-update
launch policy and generation/backup retention were not reopened.
