# Development manager internals

Use [the developer guide](../development.md) for commands and
[dpe-dev-environments](../../agents/skills/dpe-dev-environments/SKILL.md) for the
implementation workflow.

## Automatic current checkout

`run current` reserves `current` as an automatic selector, independently of the
persistent `dev` profile. `dev_current.py` fingerprints tracked/untracked build
inputs under integration/native/patches/scripts/config/ci/packaging plus root
build manifests, excluding ignored outputs. Content and executable bits participate;
branch and checkout path separate caches. Editable helper contents have their own
fingerprint; their inventory is a frozen build input so removals cannot leave
stale packaged files. Commit IDs alone are not cache identities.

ASAR assembly uses `asar_tool.command` to run only the repository-pinned ASAR
entry point through `bun --bun`, bypassing its system-Node shebang. Explicit
external ASAR executables/wrappers retain their original invocation. The helper
is included in application fingerprints; installed applications do not need Bun.

The selector validates retained prerelease compiler provenance, current runtime
fingerprints (or exact reviewed compatibility), and archive SHA-256 before reuse.
The last-good-runtime packaging exception alone cannot authorize stale native
inputs here. Artifact discovery prunes runtime/profile/source/compiler object
trees and is bounded to three levels beneath `out/`; explicit `--artifact` supports
other locations. It never dispatches a compiler or fetches mutable upstream state.
`Manager.create` performs normal portable assembly at the permanent cache path,
rebuilding project native support through its existing caches and pooling Electron
files. Assembly races discard only the newly assembled payload, not compiler caches.

Profile locking precedes resolution. A separate resolution lock serializes cache
creation. Helper publication requires catalog and exclusive version locks; unchanged
cache hits allow shared runs. A failed/partial helper refresh invalidates its record
before writing files, ensuring the next launch repairs it even after source rollback.
Checkout changes during assembly/refresh cannot publish a matching cache record.
A relaunch signal breaks the running-child wait loop immediately; it must not
wait for the old app to exit on its own. A current watch/sync run takes an exclusive version lock; watch keeps the helper
record current and refuses to refresh a different frozen build underneath that label.
Named versions retain their existing explicit sync/watch semantics. Cache entries
are managed versions with `current-checkout.json`; no automatic profile deletion
or version eviction occurs. A legacy stored `current` is preserved, not selected.

## Storage and helper identity

`build_local_app.py` extracts runtime archives into each output, then the manager
pools immutable Electron files by content hash. Managed downloads share exact,
checksum-verified upstream archives across compatibility generations. Prepared
modules stay private because adapters and native patches can differ. Cargo's
`out/rust-target` and immutable `out/rust-support` archives are separate build
caches; their size is not evidence of duplicate downloads.

Unmanaged fixture results previously retained complete runtime/profile copies.
`--reflink=auto` preserves isolation but does not give those copies a shared
lifetime or guarantee physical block sharing. Settings, popout, graphics, native
probe, frame-capture and safeStorage harnesses now own these payloads through
`fixture_storage.transient_paths`: cleanup runs after success or failure, while
reports and logs survive. Each completed cleanup writes `fixture-storage.json`.
Copying replaces a linked resources directory before packing fixture code, so a
runtime's shared application payload cannot be overwritten. Native probes retain
only required synthetic audio and voice diagnostics outside their disposable
profile; their caller also removes unpacked input distributions and modules.

Native qualification shares `out/dev/downloads` with the default managed home.
Archives use SHA-256 keys and are reverified on every cache hit. Consumers hold
the per-hash lock through extraction/copying; download pruning skips busy archives
and preserves lock-file inodes. Only recognized content-addressed `.tar.br` files
participate in `--downloads`; unrelated files are left for explicit inspection.

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
Ordinary manual launches, edits, retries and branch switches reuse that profile.
Agents create extra named/persistent profiles only for necessary isolation while
testing different versions, or at the user's explicit request for a separate
identity. Prefer sequential version tests on `dev` when isolation is unnecessary;
never copy login data. Automated checks still use disposable fresh identities.

Agents own cleanup of their test profiles when checks/comparisons finish, on
success or failure. If manual login data has been added, the profile is still in
use, or ownership is uncertain, ask about retention at the next suitable stopping
point (normally before final handback). Do not interrupt calls, delete user-owned
profiles, or leave disposable profile series for someone else to clean up.

The profile's `dpe-updater/cache` is prepared installation data, not a disposable
download cache. Generation `modules/` entries link directly into it, and
`current.json` requires those targets and their integrity records. Preserve the
complete cache dependencies of retained generations; deleting that directory can
invalidate the current client and force online reconstruction. Shared upstream
archives in `out/dev/downloads` have a separate lifetime and explicit pruning
command. Keeping login files alone does not preserve an offline-capable profile.

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
On Btrfs, use `btrfs filesystem du -s out` to distinguish exclusive and shared
extents; ordinary `du` totals do not establish exclusive physical usage.
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

Current-checkout regression coverage exercises content/inventory/branch invalidation,
profile locking before assembly, active-version publication refusal, failed-refresh
repair after rollback, source races, archive integrity, and rejection of stale
native inputs despite the last-good packaging exception. A real retained Electron
46.0.0-alpha.10 payload with pinned Canary 1.0.2483 was assembled and launched on
one fresh temporary identity, then relaunched and restarted after a helper edit.
The native login form was checked through the existing desktop CDP inspector;
no account login, messages, calls or media capture occurred. The helper edit
preserved downloaded archive mtimes and client-generation inventory. A restored
checkout then resolved the warm cache with assembly and sync explicitly forbidden.
Inputs and results: `out/dev-current-real-result.json`; raw launch/watch/cache
logs: `out/dev-current-real-{launch,watch,cache}.log`. The temporary profile was
removed after stopping its manager. This is development-manager coverage, not
release/native-media qualification or proof of every installed-view storage path.

The manual `test` profile uses managed version `latest`, pinned to Canary
1.0.2483/module revision 11 and the retained verified Electron 46.0.0-alpha.6.
Launch with `bun run dev run latest --profile test`. Its data is independent of
`dev` and `clock`; no account data was copied. The additional `alpha10` version
now uses the completed, checksum-verified Electron 46.0.0-alpha.10 artifact with the same client pins. Use
`bun run dev run alpha10 --profile test` after closing an existing run; do not
switch a running profile between versions.
