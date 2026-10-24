# Launcher settings

Use `~/.config/discord-canary-patched-electron.conf`, one flag per line. For
Stable or PTB, replace `canary` in the filename. `XDG_CONFIG_HOME` is respected.
Blank lines and `#` comments are ignored. Don't add shell quoting or assignments.

```text
# Force Xwayland.
--ozone-platform=x11
# Disable hardware video decoding for troubleshooting.
--disable-features=AcceleratedVideoDecodeLinuxGL
```

The launcher selects Wayland when `WAYLAND_DISPLAY` is set, otherwise X11.
Hardware video decoding is enabled through `AcceleratedVideoDecoder` and
`AcceleratedVideoDecodeLinuxGL`. Driver support and Chromium's GPU blocklist
still apply. Hardware encoding is not forced: it crashed the tested AMD path.
Discord's native call codecs are separate from Chromium's video APIs.

Settings apply in this order: defaults, the old package `-flags.conf`, the
channel `.conf`, then command-line arguments. Later settings win. Feature lists
merge by feature name. Set `DPE_DEFAULT_FLAGS=0` to disable the defaults.

## Troubleshooting

Close the app completely before changing environment variables:

```sh
DPE_APP_AUDIO=0 discord-canary-prerelease-patched-electron-bin
DPE_CAMERA_FAST_PROBE=0 discord-canary-prerelease-patched-electron-bin
DPE_EQUICORD=0 discord-canary-prerelease-patched-electron-bin
```

Each package has a separate profile under `~/.config/<package-name>`.
`DISCORD_USER_DATA_DIR` overrides that location.

Wayland global shortcuts require a desktop portal that supports GlobalShortcuts.
Approve its permission prompt if shown. The installed desktop entry supplies the
app identity; don't keep a user-local test entry that shadows it.

See [maintainer notes](agent/linux-launcher.md) for measured codec results,
shortcut tests and the Electron storage compatibility patch.
