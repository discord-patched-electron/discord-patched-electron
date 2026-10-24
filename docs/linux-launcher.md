# Launcher settings

Use `~/.config/discord-canary-patched-electron.conf`, one flag per line.
`XDG_CONFIG_HOME` is respected.
Blank lines and `#` comments are ignored. Don't add shell quoting or assignments.

```text
# Force Xwayland.
--ozone-platform=x11
# Disable hardware video decoding for troubleshooting.
--disable-features=AcceleratedVideoDecodeLinuxGL
```

The app uses native Wayland in a Wayland session and X11 otherwise. Hardware
video decoding is enabled when your driver supports it. Hardware encoding is
not forced.

Settings in your `.conf` override the defaults; command-line arguments override
the file. Set `DPE_DEFAULT_FLAGS=0` to turn off the default flags.

## Troubleshooting

Close the app completely before changing environment variables:

```sh
DPE_APP_AUDIO=0 discord-canary-prerelease-patched-electron-bin
DPE_CAMERA_FAST_PROBE=0 discord-canary-prerelease-patched-electron-bin
```

To remove Vencord or Equicord for troubleshooting, use its [installer](client-mods.md).

Each package has a separate profile under `~/.config/<package-name>`.
`DISCORD_USER_DATA_DIR` overrides that location.

Wayland global shortcuts require a desktop portal that supports GlobalShortcuts.
Approve its permission prompt if shown.
