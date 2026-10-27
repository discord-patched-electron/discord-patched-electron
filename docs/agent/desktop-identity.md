# Canary identity and cached Electron after application updates

## Findings on 27 October 2026

The installed `20261027.184519-1` package contained the correct yellow Canary
icon. Papirus also had a separate Canary icon. The running modded client's
Wayland `app_id`, however, was `discord`, so DMS selected regular Discord's icon.

Electron 46 reads the outer loader's package metadata before executing our entry
point. Equicord's loader declares `name: discord` without `desktopName`.
Electron's `AppPackage::Apply` consequently replaces the launcher's
`CHROME_DESKTOP=discord-canary.desktop` with `discord.desktop`. Our entry point
then passed that overwritten value back to `app.setDesktopName`.

The entry point now uses `DPE_DESKTOP_ID`, which Electron does not overwrite.
This restores the matching desktop entry and keeps icon-theme overrides working.
No tray replacement, DMS override, mod modification or Electron rebuild is needed.
Electron documents this identity in
[`app.setDesktopName`](https://www.electronjs.org/docs/latest/api/app#appsetdesktopnamename-linux).

The first-download dialog was a separate bootstrap issue. Runtime caches are
scoped to application paths so each version keeps its own immutable resources
link. Bootstrap's offline lookup missed runtimes under older application paths,
opened the dialog, and only then reused their verified files. The installed
caches had identical archive/executable hashes and preserved the original
Electron modification time, confirming reuse rather than another binary download.

Offline setup now checks the existing reuse path before failing. It still
verifies cached files, checks the runtime version and creates a separate resource
link. Missing or damaged files cannot trigger a download in offline mode. The
native dialog says “Preparing Electron...” when setup is actually needed. It
opens only if the setup helper is still running after two seconds, so quick
setup finishes without flashing a window. Cancellation still stops the helper.

## Verification

- A disposable loader with Equicord's package metadata reproduced `app_id=discord`
  using the previous entry logic and `app_id=discord-canary` with the fix. Both
  ran on native Wayland under niri with separate fresh profiles and the existing
  verified Electron 46 runtime. No account data or desktop screenshots were used.
- The runtime regression test exercises bootstrap across application generations,
  forbids downloads and the native setup helper, and checks that both versions
  retain their own resources links. A damaged shared cache is rejected offline.
- Quick setup, delayed progress, completion cleanup and cancellation are covered
  by bootstrap regression tests.
- `bun run test`: 322 Python and 75 JavaScript tests passed.
- `bun run lint`: passed.

Local fixture output and logs are in `out/desktop-identity/`.
