# Self-contained DPE direct capture permission, 2 November 2026

The Screensharing permission switch owns explicit grant and revocation. Only the
trusted settings main frame may invoke either action. Status queries never prompt
or obtain screen buffers. Grants apply to a user's active local session and the
DPE helper, independent of connector, GPU vendor, PCI identity or DRM card number.

## Confirmed failure and correction

The earlier installer completed successfully, but its unprivileged check used
`pkcheck --action-id org.freedesktop.policykit.exec --detail program …`. On this
host polkit rejected it with:

> Only trusted callers (e.g. uid 0 or an action owner) can use CheckAuthorization()
> and pass details

This made a successful installation appear to fail. Waiting longer could not fix
that check. The replacement installs the dedicated
`org.discord-patched-electron.capture` action. Its `exec.path` annotation binds it
to the fixed root-owned executable. Status and native startup query this action
**without details**; per-account rules allow that action only in an active local
session. Default policy is `no`, so ungranted capture launches do not fall back to
per-share password prompts. See the [pkcheck manual](https://www.freedesktop.org/software/polkit/docs/master/pkcheck.1.html).

## Bundled server and privilege boundary

DPE builds and ships `capture/dpe-kms-server`, alongside a checksummed manifest.
No installed GPU Screen Recorder package, global GSR capability or pre-existing
GSR rule is required. The binary uses the unchanged, reviewed GSR `099a55b` DRM
export functions, retained in `integration/video/vendor/gsr-kms-server.inc` with
provenance and the complete GPL-3.0 license. DPE's own entry point replaces the
upstream entry/protocol boundary; unused upstream entry code is discarded when
linking.

The privileged server accepts exactly a private same-user socket and a
`/dev/dri/cardN` path. It opens the card with `O_NOFOLLOW` and checks the actual DRM
node type, rather than assuming a fixed GPU or minor-number range. Both sides
verify the spawned/parent PID and expected Unix UID. One validated sequenced
socket handoff is permitted; later requests contain no descriptors and only the
fixed GET_KMS operation. Malformed requests close every received descriptor.
Output messages are bounded and nonblocking. No shell, encoder, arbitrary command,
image file or GPU render pipeline runs with privilege.

An inotify watch on the account's grant marker closes active servers when it is
revoked, including while capture is idle. Socket closure also ends the helper.
The unprivileged producer still performs the GPU snapshot and owns the PipeWire
and Discord connection.

## Explicit installation and revocation

The settings action invokes the bundled `kms-permission-setup.py --allow` or
`--revoke` through `/usr/bin/pkexec --disable-internal-agent`. The desktop
authentication agent owns the password. The installer takes the account only
from `PKEXEC_UID`; no UID, executable, policy text or destination can be supplied
through IPC or arguments. Python runs with `-I`.

After authorization it verifies root-owned, non-writable directory ancestors,
validates the exact bundled ELF against its manifest and atomically installs:

- `/usr/local/libexec/discord-patched-electron/kms-server`: the bundled server,
  root-owned and executable, without setuid or file capabilities.
- `/usr/share/polkit-1/actions/org.discord-patched-electron.capture.policy`:
  the fixed capture action and executable annotation.
- `/etc/polkit-1/rules.d/50-discord-patched-electron-kms-<uid>.rules`: one account's
  active/local permission for that action.
- `/usr/local/libexec/discord-patched-electron/permissions/<uid>`: version-2
  marker, written last.

The installer itself is never an authorized capture target. Revocation removes
only the invoking account's marker and rule; the marker is removed first so
existing helpers stop before polkit finishes reloading. Shared server/policy
assets remain for other accounts. Old version-1 grants are reported as requiring
an update and can be repaired by enabling the permission or removed by revocation.
Status allows a bounded reload period after successful installation.

This persistent permission is scoped to DPE's helper, not an Electron-only
boundary. Other programs running as the same Unix user can invoke the permitted
helper; settings explicitly warns that this is weaker isolation than the desktop
portal. No permission is installed by package installation, ordinary startup,
source discovery or tests.

## Final validation

The focused permission fixtures, native startup protocol checks and privileged
server boundary tests passed without invoking real authorization, installing root
files, opening DRM or saving pixels. Both source and server compiled successfully
under `out/picker-refinement/kms`. The native boundary fixtures use ASan/UBSan;
checks cover foreign peers, wrong node paths, malformed requests, descriptor
ownership, policy scoping, missing package dependencies, old-grant migration,
revocation and cancellation. Actual version-2 root installation and capture still
require the user's switch action; these fixtures do not claim that live step.
