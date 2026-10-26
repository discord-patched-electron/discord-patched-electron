# Launcher settings

The app uses Wayland in a Wayland session and X11 otherwise. Hardware video
decoding is enabled when your driver supports it. Most users can keep the defaults.

## Appearance and startup

Open **User Settings → OpenAsar**:

| Setting        | What it does                                                                    |
| -------------- | ------------------------------------------------------------------------------- |
| Custom CSS     | Changes Discord's appearance with your own CSS.                                 |
| Splash Theming | Uses your Discord or custom theme colors during startup. Enabled by default.    |
| Quickstart     | Opens an existing installation sooner and checks for updates in the background. |

## Global shortcuts

Wayland shortcuts, including push-to-talk, need a desktop portal that supports
GlobalShortcuts. Approve its permission prompt if shown.

## Advanced launch flags

To change them, edit `~/.config/discord-canary-patched-electron.conf` and put one
flag on each line. If you set `XDG_CONFIG_HOME`, put the file there instead.
Close Discord completely, then reopen it to apply changes.

```text
# Use Xwayland instead of native Wayland.
--ozone-platform=x11
# Disable hardware video decoding for troubleshooting.
--disable-features=AcceleratedVideoDecodeLinuxGL
```

Blank lines and `#` comments are ignored. Don't add shell quotes or assignments.
Flags in this file override defaults and OpenAsar's Focus presets.
Command-line flags take precedence over the file.

## Troubleshooting

Close Discord completely before trying an environment variable in a terminal.
For example, to disable the application-audio adapter for one launch:

```sh
DPE_APP_AUDIO=0 discord-canary-prerelease-patched-electron-bin
```

Other controls work the same way:

| Variable                  | Effect                                                    |
| ------------------------- | --------------------------------------------------------- |
| `DPE_CAMERA_FAST_PROBE=0` | Disables the camera startup helper.                       |
| `DPE_DEFAULT_FLAGS=0`     | Disables the default launch flags.                        |
| `DPE_CLIENT_OFFLINE=1`    | Skips Discord and Electron update checks.                 |
| `DPE_RUNTIME_UPDATES=0`   | Skips Electron checks while keeping the selected runtime. |
| `DPE_RUNTIME_BUNDLED=1`   | Uses the Electron version installed with the package.     |

Offline mode needs an existing installation verified against the package or an
approved release. If an older cache is rejected, launch online once to repair it.

For tarball installations, replace the command with `./launch.sh`.
To remove Vencord or Equicord for troubleshooting, use its [installer](client-mods.md).

## Profiles

The default profile is `~/.config/discordcanary`. To use another folder:

```sh
DPE_PROFILE_DIR=/full/path/to/profile discord-canary-prerelease-patched-electron-bin
```

`DPE_PROFILE_DIR` is the exact profile path. Discord's `DISCORD_USER_DATA_DIR`
variable instead selects a parent folder, with `discordcanary/` added beneath it.
Custom profiles may need the mod installer's **Custom Location** option.

Older builds used `~/.config/discord-canary-prerelease-patched-electron-bin`.
That folder is left untouched. Set `DPE_PROFILE_DIR` to its full path to keep using it.
