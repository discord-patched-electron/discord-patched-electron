# Development manager internals

Use [the developer guide](../development.md) for commands and
[dpe-dev-environments](../../agents/skills/dpe-dev-environments/SKILL.md) for the
implementation workflow.

## Storage and helper identity

`build_local_app.py` extracts runtime archives into every output. Nonportable
apps embed their output path in their ASAR and cannot safely be relocated.
Portable apps use per-profile client generations and per-installation runtime
caches. Application helpers are also copied into module sidecars. The updater's
compatibility key includes JS helpers, so ordinary edits can create another
prepared generation and download the same module archives again.

The manager imports only portable compiled local apps and checks the executable
against their local build record; this is not a fresh provenance qualification.
`create` uses the normal verified compiler-artifact path. Private dev payloads
freeze their client compatibility identity to the staged compatibility key;
this is why native/script changes require a new version. The manager stages a
private startup-worker hook before launching Electron. The splash worker runs the
normal verified client installer, then records helper symlinks in its private
module file index. Its private installation manifests likewise track symlink
destinations and omit mutable `.log` files, which otherwise invalidate a prepared
view on every launch. Native files retain content hashes. It does not disable archive checksum verification or change installed
updater behavior. Application/client/runtime auto-updates are disabled and package
pins selected during managed runs. Mods remain profile-owned.

Managed files must stay at their permanent location. The manager never edits
production profiles, source portable apps, native binaries or running modules.

## Profile lifetime and storage accounting

`run` defaults to the persistent `dev` identity independently of version selection.
`--temporary-profile` instead owns a unique managed profile for one manager run;
normal return, launch failure, Ctrl+C and SIGTERM remove it after the child process
group stops. SIGKILL or power loss can leave it behind. Temporary profiles use
normal manager locks and shared downloads. `--profile` and `--temporary-profile`
are mutually exclusive. For harnesses that relaunch or inspect state after a run,
wrap their full sequence in a temporary-directory context instead.

`doctor --profiles` finds Chromium state or DPE current-state roots under `out/`,
without following directory symlinks or descending into identified profiles.
`doctor --root PATH` selects another output tree, including external storage.
It never deletes anything and cannot infer whether an identity is expendable.
`list` reports an allocated total with hard-linked inodes counted once across the
managed home. Per-category figures overlap; reflink extents are not deduplicated.
Historical benchmark payload copies and Rust targets are outside managed pruning;
review them separately rather than evicting reusable downloads to hide growth.

## Discord link registration

Link ownership checks the effective XDG desktop entry's launch command, rather
than Electron's channel desktop ID. An installed entry is accepted when its
executable resolves to the running launcher's path. Explicit registration creates
a per-command user desktop entry and verifies the resulting association; startup
and status checks never claim it automatically.

Managed runs supply `DPE_LINK_COMMAND` with the manager's persistent `open VERSION
--profile PROFILE --` command. This is separate from `DPE_LAUNCHER`, whose temporary
signal script only restarts the current manager. Desktop activation starts a cold
profile through normal `run` handling or forwards links to the running version
with the same profile and disabled development updates. The held profile lock
records the version name; activation refuses to join a different version using
that profile. Removing the managed version leaves its desktop association stale;
select another handler through its app or system settings.

## Helper resolution

Managed links must target staged `integration/audio/` sidecars with packaged
names, not canonical `integration/video/` files: Node resolves relative imports
from the symlink target. Sync refreshes aliases before restarting. Include link
destinations in installed-view identity so repairing a cache cannot reuse a stale
view. Helper content edits retain that identity. Native/script changes need a
new version; production manifests remain content-hashed.

## Concurrent profiles and startup

Runs hold shared version locks and exclusive profile locks. Removing an active
version requires an exclusive version lock. Setup and atomic helper publication
serialize through the catalog lock. Each profile owns a relaunch script and its
process group; watchers restart their own group. Restart managers predating these
lock changes to release their old exclusive version lock.

The manager stages a private `check.py` startup hook: the normal splash worker
prepares modules and editable helper links. Shared runtime bytes get independent
resource links. Production installations do not receive this hook.

## Validation limits

Earlier manager checks established fresh launch/relaunch, real Node helper
resolution, picker IPC and watch/restart without changing archive mtimes.
Historical `out/dev-manager-check*` outputs were subsequently cleaned, so their
paths are not retained artifacts. The 8 November concurrent-profile/startup
changes had lint/whitespace checks only; concurrent GUI startup, relaunch and
watch behavior remain open until exercised with those exact inputs. A development
import is not fresh runtime provenance or release qualification.

The manual `test` profile uses managed version `latest`, pinned to Canary
1.0.2483/module revision 11 and the retained verified Electron 46.0.0-alpha.6.
Launch with `bun run dev run latest --profile test`. Its data is independent of
`dev` and `clock`; no account data was copied. The additional `alpha10` version now uses the completed, checksum-verified
Electron 46.0.0-alpha.10 artifact with the same client pins. Use
`bun run dev run alpha10 --profile test` after closing an existing run; do not
switch a running profile between versions.
