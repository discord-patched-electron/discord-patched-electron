# Launcher settings

The app uses Wayland in a Wayland session and X11 otherwise. Hardware video
decoding is enabled when your driver supports it. Most users can keep the defaults.

Software H.264 decoding uses the cached OpenH264 library. The launcher loads it
before sandbox startup so decoding works with the sandbox enabled. If the library
is missing, startup continues; restart once it is available to use software H.264.

## Appearance and startup

Open **User Settings → OpenAsar**:

| Setting        | What it does                                                                    |
| -------------- | ------------------------------------------------------------------------------- |
| Custom CSS     | Changes Discord's appearance with your own CSS.                                 |
| Splash Theming | Uses your Discord or custom theme colors during startup. Enabled by default.    |
| Quickstart     | Opens an existing installation sooner and checks for updates in the background. |

## Presets and video acceleration

Open **User Settings → OpenAsar → Flags**. Keep **Default**, or select **Custom**
to save another setup. The box at the bottom shows the flags and lets you edit
one per line. Choose **Save**, then **Restart** to apply them.

**Breaking** contains optional renderer, video decode/encode and GPU overrides,
marked with red warning icons. These may crash Discord. **Reset** restores the
selected preset; **Reload** discards your draft and reads the saved file.

Flags are stored in `~/.config/discord-canary-patched-electron.conf` (or
`XDG_CONFIG_HOME`). Existing custom edits are preserved. Save refuses to overwrite
external changes until you reload.

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

Put global overrides outside the marked OpenAsar preset section. The active
preset is inside it; other presets are remembered in comments. Keep the markers
and metadata intact. Don't add shell quotes or assignments. Global overrides win
over preset flags; command-line flags take final precedence.

The Flags box shows the complete saved list. Editing it replaces that preset's
list, so deleted flags stay deleted. Existing global entries are kept in the
other presets. Command-line arguments appear as comments for the current session.

## Troubleshooting

If a flag makes Discord crash immediately, close it completely and run:

```sh
discord-canary-prerelease-patched-electron-bin --ignore-flags
```

This skips saved presets, flag files and all supplied arguments before they are
read. It preserves your settings. Repair them in the editor, then choose
**Restart**. For tarballs use `./launch.sh --ignore-flags`;
`DPE_IGNORE_FLAGS=1` provides the same recovery mode.

Close Discord completely before trying an environment variable in a terminal.
For example, to disable the application-audio adapter for one launch:

```sh
DPE_APP_AUDIO=0 discord-canary-prerelease-patched-electron-bin
```

Other controls work the same way:

| Variable                  | Effect                                                                                                       |
| ------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `DPE_CAMERA_FAST_PROBE=0` | Disables the camera startup helper.                                                                          |
| `DPE_DEFAULT_FLAGS=0`     | Skips application defaults and the preset section; Global file overrides and command-line flags still apply. |
| `DPE_CLIENT_OFFLINE=1`    | Skips Discord and Electron update checks.                                                                    |
| `DPE_RUNTIME_UPDATES=0`   | Skips Electron checks while keeping the selected runtime.                                                    |
| `DPE_RUNTIME_BUNDLED=1`   | Selects the package's exact Electron pins, reusing the verified cache or downloading them on first use.      |

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
