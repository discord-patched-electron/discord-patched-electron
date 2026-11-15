# Local development

The dev manager keeps app versions and profiles under `out/dev/`, separate from
your installed Discord. Complete [contributor setup](../CONTRIBUTING.md#set-up)
first, then run commands from the repository root.

## Run a version

```sh
bun run dev list
bun dev run current
```

`current` resolves your current Git branch and working tree automatically,
including uncommitted and untracked build inputs. It reuses a cached payload,
refreshes changed helpers/UI, or assembles a new payload when build inputs change.
No `--sync` flag is needed. Assembly runs the pinned ASAR CLI through Bun,
so an unrelated system Node executable is not used for archive packaging. Runs reuse `dev` by default, including across versions.
Other version names shown by `list` remain pinned to their staged sources. `--profile` selects an independent login, settings
and mods; log in manually on first use. Reuse that profile when switching versions.
Stop with **Ctrl+C**. Concurrent runs need different profiles; they can share the same version.
Managed runs use pinned inputs and disable automatic DPE updates.
The startup screen stays visible while modules download and helpers are prepared.

For two instances, run these commands in separate terminals:

```sh
bun run dev run current --profile dev --watch
bun run dev run current --profile other --watch
```

Shared helper edits affect both instances; each watcher restarts its own profile.

| Mode          | Behavior                                                                          |
| ------------- | --------------------------------------------------------------------------------- |
| `run current` | Prepare the checkout automatically; reuse unchanged bundles and native artifacts. |
| `--watch`     | Refresh supported helpers and restart after edits; active calls/streams end.      |
| `--sync`      | Refresh helpers once before launch.                                               |
| Neither       | Run staged helpers unchanged.                                                     |

Automatic `current` assembly reuses a checksum-verified prerelease Electron
artifact discovered beneath `out/` (up to three directory levels), or one supplied
with `--artifact PATH`. Rust/C support builds use their existing local caches.
If Electron's native inputs changed and no matching compiled artifact exists,
the command stops with instructions; it does not launch stale code or dispatch
a hosted compiler. Retain/fetch the matching artifact, then run the same command.

Cache selection includes the checkout path, branch, frozen build inputs and
runtime identity. Helper content edits refresh the same payload; helper inventory
changes, native/launcher/patch changes and changed locks select another payload.
Returning to a previous branch/input set reuses its cached payload. A cache entry
in use cannot be refreshed beneath a running profile; close that run first.
`--watch` is optional and restarts on helper edits. If frozen build inputs change,
launch `current` again to assemble them. Existing profiles and named versions are
never replaced by cache selection; old cached versions remain visible in `list`
and can be removed explicitly. A legacy named version called `current` is retained,
but `run current` now selects the checkout rather than that old staged payload.

JavaScript/JSX, CSS, SVG, HTML, audio/video/camera/desktop Python helpers and the
diagnostic Python helpers (collector, redactor, inventory and event logger) can
refresh. Native code, shaders, patch scripts,
other updater Python, launcher changes, changed locks and removed sources require
a new version. Restart an already-running manager after changing its sync rules.

Pass Electron arguments after `--`, for example:
`bun run dev run current --profile x11-test -- --ozone-platform=x11`.

## Add a version

Import an existing portable compiled app, or assemble one from a verified runtime:

```sh
bun run dev import current out/path/to/portable-app
bun run dev create candidate --artifact out/runtime-artifact --clients config/clients.lock.json
```

Choose one command and replace the input paths. Import preserves the original
and requires `local-build.json`; AppImages, installed bootstraps and nonportable
apps cannot be imported. `create` reuses Electron rather than compiling it.
See [local packaging](agent/releases.md#package-locally) for input preparation.
Keep managed versions at their permanent paths. For automated managed launch checks,
use `bun run dev run current --temporary-profile --sync`: this creates a fresh
profile and deletes it after shutdown, including launch failures. It cannot be
combined with `--profile`. Ctrl+C and SIGTERM clean up; after a forced kill or
power loss, inspect leftovers with `doctor --profiles` and remove named managed
profiles with `remove profiles NAME`. Use a normal portable payload for release
qualification; keep fresh qualification profiles scoped to a temporary directory.
Never use a logged-in manual profile for unattended checks.

## Clean up

| Command                                 | Effect                                                              |
| --------------------------------------- | ------------------------------------------------------------------- |
| `bun run dev remove versions candidate` | Remove a version; keep its profiles.                                |
| `bun run dev remove profiles x11-test`  | Delete that profile’s login, settings, mods and caches.             |
| `bun run dev prune`                     | Preview unused shared Electron files. Add `--apply` to delete them. |
| `bun run dev prune --downloads`         | Also preview archive-cache removal; add `--apply` to delete.        |
| `bun run dev doctor --profiles`         | Find leftover Chromium/DPE profiles beneath `out/`.                 |
| `bun run dev doctor`                    | Inventory older unmanaged output directories without deleting them. |

Electron files and verified archives share storage. Sizes count each inode once
within a tree; the total in `list` also deduplicates across categories. Category
sizes still overlap and must not be added together. Reflink extents may overlap
too; allocated sizes are not a measurement of exclusive physical disk usage. Prepared modules remain profile-owned. Use `doctor --root PATH` to inspect a historical output tree on another drive;
add `--profiles` to list its leftover profiles. Put `--home PATH` before the
command to choose another fixed storage root. Run `bun run dev --help` for options;
[implementation notes](agent/dev-environments.md) cover storage and reload internals.
