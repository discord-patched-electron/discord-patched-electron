# Launcher settings

The app uses Wayland in a Wayland session and X11 otherwise. Hardware video
decoding is enabled when your driver supports it. Most users can keep the defaults.

Software H.264 decoding uses OpenH264. DPE downloads the verified library directly
from Cisco when it is missing or damaged, then loads it before sandbox startup.
Later launches reuse the library in the profile's `dpe-codecs/openh264` folder,
separate from Discord's own codec cache. If preparation fails, check your
connection and choose **Retry** in the startup window.

VP8, VP9 and AV1 also support software decoding. **HEVC requires working hardware
decoding in this build**; selecting Software video decoding prevents HEVC playback.
For NVIDIA, install and configure [nvidia-vaapi-driver](https://github.com/elFarto/nvidia-vaapi-driver#chrome),
then enable **Allow VA-API on NVIDIA GPUs** under **Advanced**.
This sets `VaapiOnNvidiaGPUs`; it does not enable encoding or bypass driver checks.
`PlatformHEVCDecoderSupport` is already enabled by default;
`AcceleratedVideoEncoder` is unnecessary for playback.
See the [codec checks](agent/codec-support.md) for tested formats and hardware.

## Appearance and startup

Open Discord's **User Settings**, then click **DPE Settings**
above **Log Out** in the left sidebar. This opens
**DPE Settings**.

| Setting                       | What it does                                                                    |
| ----------------------------- | ------------------------------------------------------------------------------- |
| Custom CSS                    | Changes Discord's appearance with your own CSS.                                 |
| Match the splash screen theme | Uses your Discord or custom theme colors during startup. Enabled by default.    |
| Quickstart                    | Opens an existing installation sooner and checks for updates in the background. |

In **Appearance → Window**, choose the native titlebar or hide window controls
together or individually. DPE removes its titlebar area when all controls are off.
Hidden buttons leave no empty slots; Help and Inbox retain the top bar's normal
edge inset.
Native titlebar takes effect next launch and
disables Discord's controls; switching it off restores your individual choices.
**Enforce minimum window size** can be turned off to resize below Discord's limit.
Controls also apply to DPE and auxiliary windows, including client-mod windows.
Native auxiliary windows use a GTK titlebar while individual controls are hidden.

**General → Media → Desktop media controls** lets Discord appear in the desktop
media player and respond to media keys. It is off by default for all Discord
media, including notification and calling sounds, videos and voice messages.
Changes apply after restarting Discord; playback inside Discord is unaffected.

**Appearance → Video popouts** controls floating windows, their titlebar, and
automatic member-bar collapse when expanding a screenshare. All three default
on. Global window controls Off also hides popout controls; extra legacy titlebar
content is preserved. New popouts fit the video and display scale, with a
bottom-right placement request. On Wayland, placement depends on the compositor;
niri also uses its window-specific IPC. Use **Show Members** to reopen the bar.
The Custom CSS editor has a 480px minimum height and grows with the window.

**General** includes **Start minimized**, **System tray**, notification flashing,
app badges and mention-only badges. These save automatically. With the tray off,
closing the main window no longer hides it in the tray, and a minimized start
stays accessible from the taskbar. Start minimized also applies to OS autostart.

## Screensharing

See the [optimized screensharing guide](optimized-screensharing.md) for game
launch options, stock hooks and system permission management.

For an experimental higher screenshare bitrate, launch with
`DPE_SCREENSHARE_MAX_BITRATE=16000000`. This requests up to 16 Mbps for eligible
H.264/AV1/HEVC shares that normally use Discord's higher-quality bitrate default. Lower-quality limits
and congestion control remain active. Server/viewer limits may prevent reaching
the requested rate. Unset the variable or set it to `0` to restore the default.

HEVC encoding uses supported VA-API hardware. On affected RX 7000 hardware, AV1
preserves the source pixels without resampling. Mesa's native 1920×1082 path
adds two black lines below a 1920×1080 source and avoids an extra padding pass.
Other unaligned sizes use centred padding where this fast path is unavailable.
DPE's Electron and dav1d software AV1 decoders automatically trim this padding
using the sender's crop metadata. Unmodified viewers retain the padding.

Enable **Experimental capture methods** in **Screensharing** to add **Optimized**
as the first tab in Discord's share picker. It lists screens as **Screen 1 (DP-2)**
and detected Vulkan/OpenGL game-capture sources. Choose a source, then use
Discord's existing quality, audio and share controls. **Vanilla** keeps the normal
system picker. Turning experimental capture off restores the original picker;
the synchronization and latency fixes remain active in both cases.

Optimized application sharing requires vkcapture. Start the game with
`~/.local/bin/dpe-game-capture -- game-command`, or use
`~/.local/bin/dpe-game-capture -- %command%` in Steam launch options.
The stable wrapper follows the installed DPE build. Restart already-running games
with the wrapper so they appear in the picker.
Stock OBS vkcapture hooks are hidden while OBS or another application owns their
capture endpoint. DPE’s bundled hooks use a separate endpoint.

**Allow direct screen capture** requests administrator authorization to enable
DPE's bundled KMS helper; installing GPU Screen Recorder separately is unnecessary.
Restricted Electron launches use a desktop user service when available; authorization
still uses the same administrator prompt and system rule.
Turning it off asks to uninstall the system rule. Turning experimental capture
off asks to remove an installed rule too. Cancelling removal keeps capture enabled. This is less isolated than PipeWire's portal:
other applications running as your Unix user can invoke the permitted helper.
The installed status describes the rule’s presence. An obsolete or blocked rule
is reported separately and does not imply direct capture is usable.

DPE Settings shows live capture and outgoing sizes separately. **Preserve bitrate in
smaller viewer windows** is off by default, keeping Discord’s normal viewer-size
and zoom adaptation. Turn it on to base bitrate headroom on your source and selected
quality instead; this can use more bandwidth. Applies to your next screenshare.
The **Incoming video** section reports each active decoder and its declared
hardware/software status; unknown decoder status appears as **Not reported**.

**Match capture to the share frame rate** skips excess PipeWire frames before
processing them. Game capture uses direct GPU buffer sharing when supported,
with a compatibility copy when export or synchronization is unavailable.
Screenshare audio controls and **Excluded codecs** are in this tab too. Codec
exclusions apply to your next outgoing screenshare, leaving camera calls and
video decoding unchanged. Keep at least one codec enabled that your system and
viewers support; DPE never silently restores an excluded codec.

During a share, this tab shows the observed capture method, codec, encoder,
resolution, frame rate and bitrate. Expand the details for skipped frames,
reported CPU or bandwidth limits, and quality adjustments. Unknown information is shown as
**Not reported**. The negotiated codec can differ from the requested codec when
the hardware or viewers require a fallback.
With no viewers, encoding may not start: DPE shows **Not started** when the native
statistics confirm zero encoded frames. A negotiated codec alone does not identify
an active hardware encoder.

## Spelling and tray icons

In **Spelling**, enable **Check spelling as you type**. System languages
are used by default. Turn off **Use system languages** to select two or more
languages together. Changes save automatically. Search by language name or code,
or filter the list to enabled languages. Words accepted in
either language do not need a manual language switch.
Turning off the last selected language turns off spell checking. Choosing a
language again turns it back on.

Electron downloads and caches Chromium dictionaries on first use; an internet
connection is needed until they are ready. Check dictionary status in this
section if suggestions are missing; a checkmark means a dictionary is downloaded.
Downloaded dictionaries work offline after restarting. The list keeps its last
results while refreshing in the background; the refresh button checks local
dictionary status without downloading the whole catalog again.
System mode disables manual language controls. Discord's existing spelling menu handles
suggestions and adding personal words.
Its **Languages** action opens DPE's **Spelling** settings. Existing message text
is checked when you enter Edit, without typing an extra space.

In **Appearance → Tray icons**, select **Discord**, **Vencord** or
**Equicord**. Presets are bundled locally and apply immediately. You can also
choose a PNG for any of the six tray states: default, unread messages, connected,
speaking, muted or deafened. Images must be
under 4 MB and no larger than 1024 × 1024 pixels. DPE copies the image into your
profile and applies it immediately. Use the **Discord**, **Vencord** and
**Equicord** links beneath each state to mix individual presets; choosing a link
replaces that state's custom image. Choosing the main preset replaces every icon, clearing custom images and individual
preset choices; **Reset** returns the row to that dropdown's preset. Each row
previews its current icon and underlines its selected preset link. Updates and
errors briefly appear below that icon's name.
The main selector shows **Custom** while any icon differs from its preset.
Vencord has default and unread artwork; its other states use the default logo.
Find asset credits and licenses under **Project links → Attributions**.
Your desktop needs a system tray to display these icons.

## Launch flags and GPU acceleration

In **DPE Settings**, select **Flags**. The Flags box shows your overrides,
one per line. App defaults are applied separately. Valid edits save automatically;
restart Discord to apply them. The sidebar shows when flags have changed, with
**Undo** to restore the values from when you opened Settings. Invalid edits show
an error and leave the last saved flags intact.

**Scrolling & mouse** includes middle-click autoscroll, disabling middle-click
paste and disabling smooth scrolling. These apply next launch. Disabling paste
overrides primary-selection paste inside DPE without changing desktop settings,
Ctrl+V, middle-click links, activity input or autoscroll.
Autoscroll forces middle-click paste off while enabled. Turning it off restores
your separate paste preference.

Expand **Advanced** for video processing, GPU/CPU rendering and driver options. **Known to crash!** marks
options that crashed in local testing; other hardware may behave differently.
Software decoding/rasterization can help isolate GPU problems but may use more CPU.
**Reset** restores launch defaults; **Reload** reads the current flags file.

Check **Edit essential flags** beside the buttons to unlock the essential-flags
box. Variables such as `{OPENH264_LIBRARY_PATH}` resolve for the current profile
on each launch. Unchecking restores the essential defaults and locks the box.

Flags are stored in `~/.config/discord-canary-patched-electron.conf` (or
`XDG_CONFIG_HOME`). Existing custom edits are preserved. Autosave refuses to
overwrite external changes until you reload.

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
**Restart Discord**. For tarballs use `./launch.sh --ignore-flags`;
`DPE_IGNORE_FLAGS=1` provides the same recovery mode.

Close Discord completely before trying an environment variable in a terminal.
For example, to disable the application-audio adapter for one launch:

```sh
DPE_APP_AUDIO=0 discord-canary-prerelease-patched-electron-bin
```

Other controls work the same way:

| Variable                          | Effect                                                                                                       |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `DPE_CAMERA_FAST_PROBE=0`         | Disables the camera startup helper.                                                                          |
| `DPE_VIDEO_QUALITY_MEASUREMENT=1` | Restores optional native VMAF/PSNR diagnostics for troubleshooting; may add video copying and decoding work. |
| `DPE_DEFAULT_FLAGS=0`             | Skips application defaults and the preset section; Global file overrides and command-line flags still apply. |
| `DPE_CLIENT_OFFLINE=1`            | Skips Discord and Electron update checks.                                                                    |
| `DPE_RUNTIME_UPDATES=0`           | Skips Electron checks while keeping the selected runtime.                                                    |
| `DPE_RUNTIME_BUNDLED=1`           | Selects the package's exact Electron pins, reusing the verified cache or downloading them on first use.      |

Offline mode needs an existing installation verified against the package or an
approved release, including its OpenH264 cache when enabled. If a cache is missing
or rejected, launch online once to prepare or repair it.

For tarball installations, replace the command with `./launch.sh`.
To remove Vencord or Equicord for troubleshooting, use its [installer](client-mods.md).

## Settings windows

Welcome uses a compact portrait window and opens floating on niri. Settings opens
in a resizable portrait window with Discord as its parent. Some Electron Wayland
versions do not pass that parent hint to the compositor; if Settings tiles, use
your compositor's initial floating rule for the title `DPE Settings`. DPE does not
change window-manager configuration or move the window after it opens.

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
