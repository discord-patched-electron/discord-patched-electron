# Capture authorization cleanup on package removal

Native packages previously removed their own files but left the separately
authorized KMS server, root maintenance verifier and per-user polkit grants.
The bootstrap now ships a standalone standard-library `uninstall.py`; removal
hooks invoke it before package files and Python dependencies disappear.

## Package coverage

| Distribution                  | Hook                                                      | Upgrade behavior                                            |
| ----------------------------- | --------------------------------------------------------- | ----------------------------------------------------------- |
| Release pacman archive        | nFPM `preremove`, embedded as `pre_remove`                | No upgrade hook                                             |
| Standalone generated PKGBUILD | `discord.install` calls `/usr/lib/<package>/uninstall.py` | No upgrade hook                                             |
| Published AUR release recipe  | `discord.install` calls `/opt/<package>/uninstall.py`     | Legacy mod refresh remains separate                         |
| Debian/Ubuntu                 | `prerm remove`                                            | `upgrade`, `deconfigure`, failed-upgrade paths skip cleanup |
| RPM                           | `%preun` when `$1` is `0`                                 | Nonzero remaining-instance counts skip cleanup              |
| Tarball/AppImage              | Explicit root cleanup before deletion                     | No package-manager removal event                            |

The AUR publisher retains a file-existence guard so regenerating metadata for
an immutable historical archive without an uninstaller does not break removal.
That historical archive cannot acquire this cleanup code without a new release.

## Scope and ordering

Cleanup serializes with installation, refresh and revocation through the existing
root `.setup.lock`. It deletes executable-grant markers before polkit rules or
helper binaries, so an active KMS server observes grant revocation. It does not
kill processes by name or modify user profiles, settings, logins or downloads.

Root-owned DPE installations under `/opt` and `/usr/lib` retain shared helper
files. When another package remains, only users whose root maintenance records
match the removed package's Discord channel and Electron track lose grants.
Another installation of the same package preserves its authorization. Legacy
unattributed records remain until the last managed installation is removed.

Debian's generated AppArmor profile is now a regular package file rather than a
retained conffile. Cleanup unloads the named profile when it is active and removes
its file, including the old conffile layout. Other applications' profiles remain.
Missing/unloaded DPE profiles need no parser invocation; ordinary upgrades skip
this path with the rest of removal cleanup.

Final removal clears all numeric UID grants, maintenance records, DPE rule files,
both capture/maintenance policy actions, the server, verifier, installer library,
root trust key, global authority, lock, known Python bytecode and interrupted
`.dpe-kms-*` atomic writes. Unknown files remain. Trusted directory checks and
bounded filename matching prevent traversal through links or writable system
directories. Deleting known leaf links removes the link alone. Repeated cleanup
is harmless, including stale policy/rules without a helper directory.

Portable installations share this global authority and cannot be discovered at
arbitrary user paths. Their explicit cleanup preserves managed package installs;
without one it removes the shared system capture setup. Removing the last managed
package also removes authority established by portable/development builds.
Using direct capture again requires setup authorization.

## Validation

Private fixtures cover final removal, per-channel removal, duplicate install
prefixes, portable cleanup, stale rules, idempotency, symlinks, unsafe directories,
lock ownership, revocation ordering and upgrade scriptlet arguments. Release
qualification now requires the uninstaller and checks native removal hooks.

`out/package-uninstall-check/` retains real DEB, RPM and Arch fixtures built with
the checksum-pinned nFPM 2.48.0. Their extracted hooks ran with a stand-in Python:
real removal invoked the expected isolated uninstaller; upgrade invocations did
not. All three archives contain the exact bootstrap uninstaller. No fixture
package was installed or removed on the host, and the installed Native16 KMS
setup was left intact. This validates archive metadata and scriptlet dispatch,
not a full package-manager transaction in every target distribution.

`bun run lint` and `bun run test` passed: 800 Python tests, 462 JavaScript
passes with 3 skips, and 92 Rust passes with 6 ignored tests. The cleanup suite
contains 12 private-filesystem cases, including AppArmor removal. Package
fingerprints include the uninstaller and both hook templates, so later cleanup
changes select a new package identity without rebuilding Electron.

References: [nFPM configuration](https://nfpm.goreleaser.com/docs/configuration/),
[Arch install scripts](https://man.archlinux.org/man/PKGBUILD.5.en),
[Debian maintainer scripts](https://www.debian.org/doc/debian-policy/ch-maintainerscripts.html).
