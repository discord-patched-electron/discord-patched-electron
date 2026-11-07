# KMS maintenance after one setup approval — 7 November 2026

Native16 replaces Native14's authentication on each changed helper/executable
with a root-owned maintenance action installed during initial setup. Existing
Native14 installations need that setup migration once. Automatic refresh never
falls back to an authenticated installer when release verification fails.

## Authority

Initial authentication installs the KMS server, root-only maintenance entrypoint,
root-owned setup library, release public key, per-user enablement record, and
fixed polkit actions. The per-user rule permits only those fixed capture and
maintenance entrypoints for an active/local user. It grants no general Python,
shell, arbitrary installer, capabilities or user-selected root executable access.

The installer pins the exact initial runtime/server/maintenance/library bytes.
That tuple can refresh its executable grant offline without another prompt.
Different code needs an exact `capture_maintenance` component record in a signed
release manifest and the independently fetched project approval record. Root
verifies the GPG signature with the installed key; channel, track, clean-source
and manifest/version checks; every incoming component hash; and user/global
rollback constraints. User-editable updater records cannot authorize root writes.
The global counter prevents another user's older cached tuple from replacing
newer shared privileged code. New release approval needs network access; the
last accepted tuple remains usable offline.

Incoming bytes are read with bounds and without following leaf symlinks. The
root maintenance process never imports or executes code from the writable
candidate application. It imports only its installed root-owned library, verifies
all candidates before replacement, and writes only fixed destinations. Approved
maintenance/library updates replace those fixed root-owned files atomically.
A failed copy leaves capture disabled until repair rather than restoring an
old permissive grant. Explicit revocation disables maintenance before removing
the marker/rule; re-enabling requires setup approval.

## Caller and executable checks

Maintenance authenticates the live Discord gate using kernel `SO_PEERCRED` and
a per-request capability. The supplied PID must own that gate and belong to the
invoking Unix user. The selected application path is attested over that authenticated connection;
the maintainer compares it with the requested candidate. Root hashes the live executable through
procfs and rechecks the exact inode/change metadata before publishing its grant.
A different executable or an older application cannot nominate another app's
approved files as its own active installation.

The installed KMS server is mode **0744**, so ordinary users cannot execute it
directly. pkexec supplies root execution through its fixed action. Before DRM
export, the existing Rust boundary still requires a live capability gate from
the one currently permitted Discord executable and checks the exact worker.
Changing the grant retires the previous executable and active exports. Worker
close/Discord exit/revocation still win over simultaneous frame requests. The
new maintenance path adds no per-frame hashing or network work.

This is an executable/code-selection boundary, not isolation from software
already controlling the permitted Discord process or running other code through
the same approved Electron binary. Modified/unsigned development payloads need
explicit administrator approval; the automatic path rejects them.

## Qualification

The real signature fixture uses an ephemeral signing key and GPG/GPGV; tampered
manifests, forged approvals and changed checksum lists are rejected. Tests cover
kernel PID/capability matching, initial application identity, exact current and
newly approved tuples, disabled grants, global rollback, fixed installation
modes, initial setup, callback serialization and rejection without an auth fallback.
The release packaging fixture verifies component hashes against the complete
application before its installer payload is reduced.

Lint, 787 Python tests, 462 JavaScript passes (three skips), 92 Rust passes and
the real screensharing settings harness pass. Native16 is built from the retained
verified Electron runtime. The corrected authority is installed. An explicit privileged refresh returns
`{"updated": true}` without authentication. A fresh managed activation also moves
the executable grant automatically; no authenticated installer is launched.
Direct execution of the installed mode-0744 KMS helper is denied by the kernel.
Private evidence is
under `out/live-capture-private/kms-maintenance-*` and `native16-*`.

The first Native15 system refresh failed because Electron clears its procfs
environment. That rejection is retained. Native16 replaces the invalid environment
assumption with the selected application identity sent by the kernel-authenticated
Discord gate. A real socket/crypto/refresh regression covers that complete path
without depending on procfs environment contents. Native15 launch/relaunch had
passed; those checks alone did not qualify maintenance.

The fresh Native16 launch/relaunch passes and the watcher records a grant move
from dev to the fresh profile's executable with zero authentication-installer
processes. The maintenance action returns the grant to dev silently. A forged
request from an unrelated Node executable and another from the older clock DPE
application both exit 1 without changing the grant. Four direct thumbnail
requests from current DPE produce four changing hashes; the older DPE caller
receives zero bytes. A systemd-hosted thumbnail also succeeds.

The final live KMS call delivers 613 receiver frames in 10.25 seconds and four
changing frame hashes. Its preview advances 19→94, with 672 conversions and
665 GPU encoder inputs; capture drops/failures, full-resolution CPU readbacks,
and preview errors are zero. The retained AV1 runtime still decodes this case
as 1920×1082. Both accounts disconnect and owned capture/exporter processes exit.
No user pixels are saved; thumbnail tests retain only hashes and lengths.
