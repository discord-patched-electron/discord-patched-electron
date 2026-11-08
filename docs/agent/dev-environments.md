# Development environment manager: implementation and evidence

For commands, profiles, reload coverage and cleanup, see the
[developer guide](../development.md). Agent workflow instructions live in
[dpe-dev-environments](../../agents/skills/dpe-dev-environments/SKILL.md).

## Design and investigation

The initial inventory on 2026-11-05 found about 179 GiB under `out/`, including
29 GiB in `client-2444-compat`, 25 GiB in `capture-implementation-20261102`, and
multiple 9–13 GiB experiment directories. This is historical evidence, not a
cleanup allowlist. No historical outputs or account profiles were deleted.

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
Future work could add a graphical selector, shared extracted unmodified Discord
modules and renderer-only refresh where lifecycle hooks support disposal.

## Validation (2026-11-05)

- Repository lint and full Python/JavaScript suites pass (719 Python tests;
  430 JavaScript passes, three skips).
- Nine manager regressions cover path restrictions, locks, profile preservation,
  failed creation cleanup, sharing/pruning, atomic sync and editable manifest
  semantics.
- Imported the retained `experimental-codecs-manual/app` portable app without
  changing its source. This is an older Canary 1.0.2357 baseline, not qualification
  of current native patches or the latest client lock.
- `check_desktop_launch.check` through a manager wrapper, a fresh `smoke2` profile
  and watch mode passed login, native module APIs, codec preload and popouts.
  Welcome's restart returned through the manager. Evidence is local under
  `out/dev-manager-check2/` and `out/dev-manager-check3/` (after runtime sharing).
- A real temporary helper content edit triggered watch/restart, reused all archive
  mtimes and retained the same installation view set. Source contents and timestamps
  were restored afterward. Evidence: `out/dev-manager-watch.log`.
- No account login, messages, calls, physical media capture or historical cleanup.
  Verified-artifact `create` was not exercised end to end because no extracted
  compiler artifact was present; it uses the existing build/provenance path.

Test-only managed versions, profiles and cached objects/archives were removed
after validation. Reports and original portable inputs were retained.

## Fresh working-tree setup after cleanup (2026-11-05)

At the user's request, all previous `out/` contents and temporary dev-test logs
were removed, closing the two old test app instances first. The historical local
output/report paths above no longer exist. Compiler workspaces outside `out/`
were preserved.

Created managed version `current` from the working tree using
`config/clients.lock.json` (Canary 1.0.2357) and the verified retained Electron
46.0.0-alpha.1 artifact pinned in `config/update-policy.json`. Built/staged the
current native integration helpers and prepared fresh profile `dev`; no account
login or onboarding choices were made in that profile. Electron itself was reused,
not recompiled from the working tree.

Managed launch and relaunch passed with a separate fresh `verification` profile,
including login, native module APIs, codec preload and popouts. The verification
profile, reports and temporary build directories were then deleted as requested;
`out/` retains only the new managed environment and its shared download/runtime
caches. Start it with `bun run dev run current --profile dev --watch`.

## Picker helper resolution fix (2026-11-05)

Managed voice helpers linked directly to canonical source files. Node followed
those links into `integration/video/`, where renamed imports such as
`./dpe-screenshare-settings.cjs` do not exist. Capture backend setup stopped before
registering its picker listener, producing the 15-second selection timeout.

Links now target the staged `integration/audio/` sidecars with their packaged
names. Sync refreshes those aliases before restart. The next manager run repairs
existing profile links; no native rebuild or profile reset is needed. Restart
the manager itself to load the updated watch implementation.

Validation: real Node module resolution and picker RPC regression, including
migration and helper edits; an isolated Electron 46 check with the prepared
Canary 1.0.2357 voice module and real native picker hooks; lint; 720 Python tests
and 430 JavaScript passes (three skips). Evidence: `out/picker-helper-check/`,
`out/picker-helper-tests.log` and `out/picker-helper-lint.log`. The Electron check
exercised page/preload events and IPC without logging in or capturing a source.
Live account sharing remains unverified. Existing user profiles were untouched.

### Installed-view follow-up

The first repair corrected the cache, but `prepare_view` could reuse an installed
view still linked to canonical sources. Private dev installations now include
helper link destinations in their identity. Restarting republishes the corrected
view once; helper content edits retain the same identity. Production installation
rules remain unchanged.

The regression reproduces stale reuse, then checks migration, publication and
reuse after a content edit. The current dev profile’s published helper paths were
also checked. Picker loading and errors now reuse the centered icon state instead
of showing plain text alongside contradictory empty-state content. The real
settings harness covers light/dark themes and narrow zoomed windows under its
`capture-picker` scope; evidence is in `out/picker-state-ui/`.

## Concurrent profiles on one version (2026-11-08)

Managed runs hold shared version locks and exclusive profile locks. Removing an
active version still requires an exclusive version lock and is rejected. Launch
setup and helper sync serialize through the catalog lock. Startup helper copies
publish atomically so another profile cannot import a partial file.

Each profile owns its relaunch script, which signals its manager and is removed
after shutdown. Watchers share the staged helper files but independently restart
their own process groups. Restart managers started before this change to release
their old exclusive version locks.

Validation: repository lint and whitespace checks; tests and desktop checks were
skipped at the user's request. Concurrent GUI startup, relaunch and watch behavior
remain for human verification.
