# Local development

The development manager keeps reusable DPE app versions separate from Discord
profiles. Switch versions with the same dev login and settings, refresh helpers
while editing, and clean up test runs without touching your installed Discord.

Complete the [contributor setup](../CONTRIBUTING.md#set-up) first. Run these
commands from the repository root on Linux.

## Add a version

Import a full portable app previously assembled with `build_local_app.py
--portable`:

```sh
bun run dev import current out/path/to/app
```

Replace `out/path/to/app` with your app directory. Import preserves the original.
It requires a compiled local app with `local-build.json`; installed bootstrap
packages, AppImages and older nonportable local builds cannot be imported.

If you have a verified compiler artifact, assemble a version directly:

```sh
bun run dev create candidate --artifact out/runtime-artifact --clients config/clients.lock.json
```

This reuses Electron rather than compiling it. See the
[local packaging guide](agent/releases.md#package-locally) for preparing build
inputs. Managed versions are for development; use normal packaging for release
qualification.

## Run and edit

```sh
bun run dev run current --profile dev --watch
```

The profile is created on first use. `--watch` refreshes supported source files,
bundles the settings UI and restarts Discord when edits settle. JavaScript, JSX,
CSS, SVG, HTML and audio/video/camera/desktop Python helpers can refresh this way.
Restarting ends active calls and streams.

Use `--sync` to refresh once. Omit both options to run the staged sources unchanged.
Native C/C++, shaders, patch scripts, updater Python, launcher changes, changed
locks and removed source files require a new version.

Switch to another version with the same profile after stopping the first run:

```sh
bun run dev run candidate --profile dev
```

Close the managed app with **Ctrl+C** in its terminal. The manager prevents two
managed runs from using the same profile or version simultaneously. To run two
apps concurrently, choose different versions and profiles. Use a fresh profile
for automated checks, and launch managed profiles through the manager so its
locks apply.

Pass Electron arguments after `--`:

```sh
bun run dev run current --profile x11-test -- --ozone-platform=x11
```

Managed runs select their pinned Discord and Electron inputs and disable automatic
DPE updates. Changes to those inputs belong in another version.

## Storage and cleanup

Everything lives in `out/dev/` by default. Put `--home PATH` before the command
to use another location, for example `bun run dev --home /path/to/dpe-dev list`.
Keep that location fixed after creating versions.

Electron files share storage between versions and matching profile installations.
Verified Discord download archives are reused across versions and profiles;
prepared modules remain profile-owned. Size listings include shared links, so
adding them together overstates unique disk use.

```sh
bun run dev list
bun run dev remove versions candidate
bun run dev prune
bun run dev prune --apply
```

Removing a version preserves profiles. `prune` previews unused shared Electron
objects; `--apply` removes them. Add `--downloads` to evict cached archives too,
which makes future setup download those archives again.

To delete a disposable profile, including its **login, settings, mods and caches**:

```sh
bun run dev remove profiles x11-test
```

`bun run dev doctor` lists historical `out/` directories and their sizes. Those
older runs are unmanaged: inspect them before removing them manually. The manager
does not clean them automatically.

Run `bun run dev --help` for all commands. Implementation details and recorded
checks live in the [agent notes](agent/dev-environments.md).
