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

Open **User Settings → OpenAsar → Launch presets and flags**. Select **Balanced**
for Chromium's defaults, **Responsive** to keep background work active, or
**Efficiency** to explicitly retain background timer throttling. Each preset has
an editable flags box and its own optional controls. Save, then restart to apply.

Presets are stored in `~/.config/discord-canary-patched-electron.conf` (or
`XDG_CONFIG_HOME`). Existing OpenAsar settings and flags are imported automatically;
the original file is backed up. **Reload from file** discards your draft and reads
external edits. Save refuses to overwrite a file changed since you loaded it.

Hardware decoding and encoding have separate checkboxes. Unchecked means use
the runtime's defaults. Rendering backends, driver overrides and other experiments
are optional and off by default. Forced encoding currently crashes on the tested
AMD RX 7900 GRE; leave it off unless testing. These controls affect Chromium media,
not Discord's separate call codec engine. Obsolete controls are removed with a
notice while preserving your other edits.

**Force acceleration and bypass GPU checks** requests GPU painting and hardware
decoding while bypassing the GPU blocklist and video vendor checks. Encoding has
its own checkbox. Under **Work-in-progress options**, you can explicitly force
Graphite or compositor memory buffers. Each option shows its flags, is saved per
preset, and starts off. Graphite currently crashes on the tested AMD GPU; recovery
with `--ignore-flags` skips these options too.

**Next launch** shows the merged arguments and their sources. One flag goes on
each line; blank lines and `#` comments are allowed. Manual flags override preset
checkboxes. **Restore this preset's defaults** changes the draft until you save.

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

Put global overrides outside the marked OpenAsar preset section; they appear in
**Additional flags**. Active preset flags are visible inside that section, with
other presets remembered in comments. Editing the active lines directly loads
them as preset text and resets that preset's option controls. Keep the section
markers and metadata comment intact. Don't add shell quotes or assignments.
Global overrides win over preset flags; command-line flags take final precedence.

## Troubleshooting

If a flag makes Discord crash immediately, close it completely and run:

```sh
discord-canary-prerelease-patched-electron-bin --ignore-flags
```

This skips saved presets, flag files and all supplied arguments before they are
read. It preserves your settings. Repair them in the editor, then choose
**Restart with saved flags**. For tarballs use `./launch.sh --ignore-flags`;
`DPE_IGNORE_FLAGS=1` provides the same recovery mode.

Close Discord completely before trying an environment variable in a terminal.
For example, to disable the application-audio adapter for one launch:

```sh
DPE_APP_AUDIO=0 discord-canary-prerelease-patched-electron-bin
```

Other controls work the same way:

| Variable                  | Effect                                                                                                  |
| ------------------------- | ------------------------------------------------------------------------------------------------------- |
| `DPE_CAMERA_FAST_PROBE=0` | Disables the camera startup helper.                                                                     |
| `DPE_DEFAULT_FLAGS=0`     | Skips application defaults and the preset section; Additional flags and command-line flags still apply. |
| `DPE_CLIENT_OFFLINE=1`    | Skips Discord and Electron update checks.                                                               |
| `DPE_RUNTIME_UPDATES=0`   | Skips Electron checks while keeping the selected runtime.                                               |
| `DPE_RUNTIME_BUNDLED=1`   | Selects the package's exact Electron pins, reusing the verified cache or downloading them on first use. |

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
