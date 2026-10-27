# Linux launcher, codecs and shortcuts

For user settings, see [launcher settings](../linux-launcher.md). This page explains
flag precedence, the tested graphics behavior and native shortcut integration.

## OpenAsar version label

The settings label uses `release_version` from the installed package's
`runtime-update.json`: `OpenAsar (discord-patched-electron VERSION)`. Local builds
without a release version show `development`. The renderer adapter supplies this
label without changing the pinned OpenAsar source; its unstamped `nightly` string
previously produced an empty version.

## Flag handling

`integration/launcher/flags.py` resolves flags before Electron starts, in this order:

1. Application identity and defaults.
2. Selected preset's optional controls, then its editable flags.
3. Additional flags shared by all presets.
4. The older package-specific `-flags.conf`.
5. `~/.config/discord-canary-patched-electron.conf` (respecting `XDG_CONFIG_HOME`).
6. Original command-line arguments.

Feature lists merge by feature name; the last setting wins. Other Chromium
switches keep their normal last-value precedence. `DPE_DEFAULT_FLAGS=0` skips
application defaults and all saved presets/options. The managed launcher suppresses
OpenAsar's subsequent flag injection entirely. The direct legacy compatibility
adapter still drops its automatic `EnableDrDc` and `--ignore-gpu-blocklist` settings.

The launcher selects `--ozone-platform=wayland` when `WAYLAND_DISPLAY` is present,
otherwise `--ozone-platform=x11`. Hardware decode follows Chromium's defaults;
there are separate optional decode/encode overrides. `--ignore-flags` or
`DPE_IGNORE_FLAGS=1` skips every user flag source before parsing it. See the
[preset design, research and current results](launch-presets.md).

## Graphics results and limits

The [OpenAsar playback investigation](video-playback.md) reproduced GPU crashes
with the default Performance preset on Electron 46. Removing `EnableDrDc`, which
Chromium explicitly does not support on Linux, restored playback on Wayland and
Xwayland. The adapter also keeps driver blocklists enabled by default.

On the tested AMD RX 7900 GRE, synthetic playback used `VaapiVideoDecoder` and
WebGL used hardware ANGLE/OpenGL. Software WebCodecs encoders for H.264, VP8, VP9
and AV1 each produced twelve frames.

Forcing `AcceleratedVideoEncoder` crashed the GPU process in
`CreateGpuMemoryBufferHandle`, including with native GPU buffers enabled. It
remains off by default. An earlier `--ozone-platform=auto` run failed; the current
flags-free recovery check succeeds with native Wayland selected by Electron.
Normal launches retain explicit session selection. GPU blocklists and the GPU
sandbox remain enabled; Vulkan is not forced.

Discord's voice/video module has a separate codec stack. Its pre-call report
showed VA-API and H.264/H.265/AV1 decoding support, plus VP8 encode/decode.
Neither that report nor WebCodecs tests establish hardware encoding in a call.
See [desktop test evidence](../evidence/desktop-fixes-local.json).

## Wayland shortcuts

The pinned `discord_utils` module uses the GlobalShortcuts portal directly,
including press/release events for push-to-talk and Wayland shortcut recording.
Electron's `globalShortcut` is a separate API; its feature flag alone cannot
repair Discord's native path.

`scripts/desktop_identity.py` supplies the standard Canary identity:
`discord-canary`. The desktop file is `discord-canary.desktop`, matching the
bootstrap, `DISCORD_PORTAL_APP_ID` and `CHROME_DESKTOP` settings. Older local builds
used a reverse-DNS identity; those entries may need removing if they shadow the
system installation.

The local builder emits a desktop file beside `launch.sh`. For a non-portable
local build, it can be copied to `~/.local/share/applications/` for portal discovery.
A portable build's desktop entry targets the installed system command.

A private D-Bus fixture checked registration, Ctrl+Alt+F12 and `true`/`false`
press/release callbacks without grabbing desktop keys or changing permissions.
To reproduce in a Wayland session, install system `dbus-python` and PyGObject:

```sh
uv run --no-project --python /usr/bin/python3 python scripts/check_shortcuts.py \
  --runtime /path/to/runtime/electron \
  --utils /path/to/modules/discord_utils/discord_utils.node \
  --output /path/to/fresh-shortcut-check
```

This command uses system Python so those bindings are available. The actual
desktop still needs a GlobalShortcuts backend and any requested user permission.
Real keyboard interaction on GNOME/KDE remains untested. Unsupported mouse buttons
or key combinations do not gain an X11 fallback.

References: [Electron's Linux shortcut identity](https://www.electronjs.org/docs/latest/api/global-shortcut#usage-on-linux),
[portal press/release protocol](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.GlobalShortcuts.html).

## Storage compatibility

Electron 46 removes synchronous `safeStorage` methods used by the pinned desktop
module. A sidecar keeps Discord's synchronous renderer IPC while awaiting the new
main-process API, including its structured decrypt result. Locked or unavailable
storage returns false/null without adding a plaintext fallback. Older Electron
methods remain supported.

`scripts/check_safe_storage.py` checks real IPC, invalid ciphertext, null values
and decryption after relaunch. Only this isolated fixture uses the basic password
store and a synthetic string; the app keeps its normal OS key store. Unknown
core IPC layouts fail preparation for review.
