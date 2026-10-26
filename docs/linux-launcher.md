# Launcher settings

The app uses Wayland in a Wayland session and X11 otherwise. Hardware video
decoding is enabled when your driver supports it. Most users can keep the defaults.

## Appearance and startup

Open **User Settings → OpenAsar** for custom CSS, splash theme colors and
Quickstart. Splash Theming is enabled by default and uses your Discord or custom
theme colors. Quickstart opens an existing installation sooner and checks for
updates in the background.

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

- `DPE_CAMERA_FAST_PROBE=0` disables the camera startup helper.
- `DPE_DEFAULT_FLAGS=0` disables the default launch flags.
- `DPE_CLIENT_OFFLINE=1` skips both client and Electron update checks. You need
  an existing installation verified against the package or an approved release.
  If an older cache is rejected, launch online once to repair it.
- `DPE_RUNTIME_UPDATES=0` skips Electron checks while keeping the selected runtime.
- `DPE_RUNTIME_BUNDLED=1` uses the Electron version installed with the package.

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
