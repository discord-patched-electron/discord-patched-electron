# KMS helper updates and executable grants — 7 November 2026

This records Native14. [Native15](kms-silent-maintenance-20261107.md) replaces
its repeated authentication with a narrow maintenance authority installed once.

The updater refreshes an existing direct-capture grant when the activated
Discord executable or bundled KMS server changes. It runs after Electron is
ready, including offline activation of a prepared update. Background downloads
leave the running helper and grant alone. It does not enable capture for a user
who has never granted it.

Privileged installation still uses desktop authentication. The updater records
one automatic attempt per helper/executable identity in the profile. Cancellation
retains the installed state and does not reopen the dialog on every relaunch;
**Update permission/helper** in Screensharing retries manually. Another activated
executable/helper identity gets its own automatic attempt. Failure to persist the
attempt record leaves the manual action available instead of repeatedly prompting.

## Grant and worker boundary

`DPE-KMS-3` replaces the user-wide version-2 marker with a root-owned record of
the running executable's exact path, device, inode, size, modification time and
change time. The installer reads the supplied live Discord PID through procfs,
checks its owner and ELF inode, and detects path replacement before publication.
Changing the executable or its metadata invalidates new capture authorization.
This is intentionally conservative, including inode metadata changes from
hard-link maintenance in development builds.

The installer removes the old marker before replacing the privileged server,
policy and rule, then publishes one complete grant. There is no list of older
permitted binaries. Old active exporters see marker removal through inotify and
stop. A failed installation cannot leave an old permissive marker paired with a
new server. Cancelling authentication before installation leaves the previous
grant intact; the new executable cannot use that grant.

Each KMS worker and KMS thumbnail session gets a private Unix socket and random
256-bit capability from Discord's main process. Before opening DRM, the Rust
server checks the gate's kernel `SO_PEERCRED`, matches that process's live and
path-resolved executable inode/metadata against the root grant, and validates
the capability and exact worker executable. This also works when systemd launches
the worker outside Discord's process tree. Missing proof fails closed; there is
no old-marker, direct-CLI or ancestor-name fallback.

The authorization stream stays open for the worker lifetime. Its close and the
grant watch are polled alongside frame requests, and both beat a simultaneously
ready request. Authentication performs no per-frame executable scans or pixel
copies. Shutdown destroys the gate, closes its clients, and retires the worker's
capability. Active sessions do not continuously rehash/reinspect the executable;
normal updater activation revokes them by replacing the grant.

The fixed polkit action still authorizes launching the narrow root helper for
an active/local user. Access enforcement happens inside that helper before any
DRM export; the polkit rule alone is not the binary restriction. This is not a
sandbox against software already able to modify or control the authorized
Discord process, or to execute other code through the same Electron binary.
No `CAP_SYS_ADMIN`, setuid bit or broad passwordless installer is added to Discord.

## Checks

Focused tests cover installer payload verification, exact executable ownership,
legacy/wrong-executable rejection, updater activation, shared pending work,
cancellation persistence/manual retry, real Unix capability gates, host-service
environment forwarding, KMS thumbnail authorization, simultaneous revocation,
and Discord exit. The Rust credential fixture uses actual kernel peer credentials
and rejects a mismatched inode or worker. The C startup fixture exercises the
new argument contract and existing cancellation/FD cleanup.

The real screensharing settings harness passes against retained Electron
`6d0e3db61278f7be23afbb0129361d6fe4d30c9547e7fe5d1248740b9af57cb5`.
Private build, full-suite and activation evidence is under
`out/live-capture-private/kms-updater-*` and `native14-*`.

Native14 is built and runs as `capture-latest` with the existing dev profile.
Lint, 781 Python tests, 459 JavaScript passes (three skips), 92 Rust passes, and
the real settings harness pass. The automatic migration did open the expected
system dialog. Its first attempt timed out; the manual retry then completed
system authentication and installed the exact Native14 server
`740df4cd5b57ac89fb3410e3eeeef41a9448f4d1b11cf3da631913a0b328acaf`.
The root-owned version-3 marker matches the running dev executable's complete
identity. There is one executable grant for this Unix user.

The packaged live KMS share delivers 608 receiver frames in 10.17 seconds, with
four changing owned-frame hashes, preview progress 18→95, 668 conversions and
660 GPU encoder inputs. Capture drops, failures, full-resolution CPU readbacks
and preview errors are zero. Both accounts disconnect afterward.

Four direct KMS thumbnail requests from the authorized executable return changing
320×180 frames. The same new helper and capability code loaded in the older
clock-profile Discord process returns zero pixels for all four requests. An
unrelated Node process also receives zero pixels and exit status 1. A separate
explicit systemd-hosted thumbnail succeeds with 230,420 stdout bytes (the bounded
header plus 320×180 BGRA); only its hash/length is retained. Thus the installed
server accepts the host path while rejecting callers outside the executable grant.
All owned capture workloads and privileged exporter sessions exit afterward.

A managed Native14 relaunch after migration retains the exact executable grant and opens
no new installer/authentication process. A fresh systemd-hosted KMS thumbnail
also succeeds after relaunch, confirming that the grant follows the executable
rather than the original Discord PID.
