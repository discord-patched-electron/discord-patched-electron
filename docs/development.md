# Local development

The dev manager keeps app versions and profiles under `out/dev/`, separate from
your installed Discord. Complete [contributor setup](../CONTRIBUTING.md#set-up)
first, then run commands from the repository root.

## Run a version

```sh
bun run dev list
bun run dev run current --profile dev --watch
```

Use a version shown by `list`. `--profile` selects an independent login, settings
and mods; log in manually on first use. Reuse that profile when switching versions.
Stop with **Ctrl+C**. Concurrent runs need different versions **and** profiles.
Managed runs use pinned inputs and disable automatic DPE updates.

| Mode      | Behavior                                                                     |
| --------- | ---------------------------------------------------------------------------- |
| `--watch` | Refresh supported helpers and restart after edits; active calls/streams end. |
| `--sync`  | Refresh helpers once before launch.                                          |
| Neither   | Run staged helpers unchanged.                                                |

JavaScript/JSX, CSS, SVG, HTML and audio/video/camera/desktop Python helpers can
refresh. Native code, shaders, patch scripts, updater Python, launcher changes,
changed locks and removed sources require a new version.

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
Keep managed versions at their permanent paths. Use a fresh profile for automated
tests and a normal portable payload for release qualification.

## Clean up

| Command                                 | Effect                                                              |
| --------------------------------------- | ------------------------------------------------------------------- |
| `bun run dev remove versions candidate` | Remove a version; keep its profiles.                                |
| `bun run dev remove profiles x11-test`  | Delete that profile’s login, settings, mods and caches.             |
| `bun run dev prune`                     | Preview unused shared Electron files. Add `--apply` to delete them. |
| `bun run dev prune --downloads`         | Also preview archive-cache removal; add `--apply` to delete.        |
| `bun run dev doctor`                    | Inventory older unmanaged output directories without deleting them. |

Electron files and verified archives share storage; per-tree sizes overcount
shared files. Prepared modules remain profile-owned. Put `--home PATH` before the
command to choose another fixed storage root. Run `bun run dev --help` for options;
[implementation notes](agent/dev-environments.md) cover storage and reload internals.
