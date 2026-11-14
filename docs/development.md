# Local development

The dev manager keeps app versions and profiles under `out/dev/`, separate from
your installed Discord. Complete [contributor setup](../CONTRIBUTING.md#set-up)
first, then run commands from the repository root.

## Run a version

```sh
bun run dev list
bun run dev run current --watch
```

Use a version shown by `list`. Runs reuse `dev` by default, including across
versions. `--profile` selects an independent login, settings
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

| Mode      | Behavior                                                                     |
| --------- | ---------------------------------------------------------------------------- |
| `--watch` | Refresh supported helpers and restart after edits; active calls/streams end. |
| `--sync`  | Refresh helpers once before launch.                                          |
| Neither   | Run staged helpers unchanged.                                                |

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
