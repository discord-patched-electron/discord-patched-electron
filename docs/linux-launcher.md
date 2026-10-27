# Launcher settings

The app uses Wayland in a Wayland session and X11 otherwise. Hardware video
decoding is enabled when your driver supports it. Most users can keep the defaults.

Software H.264 decoding uses the cached OpenH264 library. The launcher loads it
before sandbox startup so decoding works with the sandbox enabled. If the library
is missing, startup continues; restart once it is available to use software H.264.

VP8, VP9 and AV1 also support software decoding. **HEVC requires working hardware
decoding in this build**; selecting Software video decoding prevents HEVC playback.
For NVIDIA, install and configure [nvidia-vaapi-driver](https://github.com/elFarto/nvidia-vaapi-driver#chrome),
then enable **Allow VA-API on NVIDIA GPUs** under **Advanced**.
This sets `VaapiOnNvidiaGPUs`; it does not enable encoding or bypass driver checks.
`PlatformHEVCDecoderSupport` is already enabled by default;
`AcceleratedVideoEncoder` is unnecessary for playback.
See the [codec checks](agent/codec-support.md) for tested formats and hardware.

## Appearance and startup

Open Discord's **User Settings**, then click **OpenAsar (discord-patched-electron …)**
in the version information at the bottom of the left sidebar. This opens
**DPE OpenAsar Config**.

| Setting        | What it does                                                                    |
| -------------- | ------------------------------------------------------------------------------- |
| Custom CSS     | Changes Discord's appearance with your own CSS.                                 |
| Splash Theming | Uses your Discord or custom theme colors during startup. Enabled by default.    |
| Quickstart     | Opens an existing installation sooner and checks for updates in the background. |

## Launch flags and GPU acceleration

In **DPE OpenAsar Config**, select **Flags**. The Flags box shows your overrides,
one per line. App defaults are applied separately. Choose **Save**, then
**Restart** to apply changes. Save stays disabled until something changes.

Expand **Advanced** for video processing, GPU/CPU rendering and driver options. **Known to crash!** marks
options that crashed in local testing; other hardware may behave differently.
Software decoding/rasterization can help isolate GPU problems but may use more CPU.
**Reset** restores launch defaults; **Reload** discards your draft.

Check **Edit essential flags** beside the buttons to unlock the essential-flags
box. Variables such as `{OPENH264_LIBRARY_PATH}` resolve for the current profile
on each launch. Unchecking locks the box; its flags still apply.

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

Put global overrides outside the marked OpenAsar section. Keep its markers and
metadata intact. Don't add shell quotes or assignments. Global overrides win
over the managed flags; command-line flags take final precedence.

Clearing the Flags box keeps the app's required defaults. Command-line arguments
appear as comments for the current session.

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

For a fresh test profile, use a folder on disk instead of `/tmp`, which may have
a separate memory or per-user quota even when your drive has free space:

```sh
mkdir -p "$HOME/.cache/dpe-tests"
test_dir=$(mktemp -d "$HOME/.cache/dpe-tests/test.XXXXXX")
mkdir -p "$test_dir/tmp"
TMPDIR="$test_dir/tmp" \
DPE_PROFILE_DIR="$test_dir/profile" \
EQUICORD_USER_DATA_DIR="$test_dir/profile/equicord" \
discord-canary-prerelease-patched-electron-bin
```

For tarballs, replace the last line with the path to `launch.sh`. Each run creates
a new Discord profile; keep its path if you want to reuse it. Desktop settings
and the shared launch-flags file remain in your normal configuration directory.
Keep `XDG_CONFIG_HOME` unchanged: overriding it also hides your default browser
association and can stop links from opening.

Older builds used `~/.config/discord-canary-prerelease-patched-electron-bin`.
That folder is left untouched. Set `DPE_PROFILE_DIR` to its full path to keep using it.
