# Screen-share audio

On Wayland, share a window in Discord and enable audio.

Sharing an application includes audio from all its windows and tabs. For example,
sharing one Zen window also shares audio from its other windows. Browser audio
cannot be separated by tab or window.

## Automatic detection

DPE matches the selected portal stream to Discord's active video connection.
When it identifies a window, it selects that application's audio. A confirmed
whole-monitor share selects system audio, excluding Discord.

| Desktop / backend                                   | Window detection | Detection paths                                                                                                                                                         |
| --------------------------------------------------- | ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| niri                                                | ✅ Automatic     | Direct active-cast and window-process mapping on supported versions.                                                                                                    |
| KDE Plasma                                          | ⚠️ Dependencies  | `kdotool` enables KWin stream mapping. Portal app IDs can also match audio metadata without it.                                                                         |
| GNOME / Mutter                                      | ⚠️ Dependencies  | [Window Calls](https://extensions.gnome.org/extension/4724/window-calls/) enables exact shared-window mapping. Portal app IDs can also match audio metadata without it. |
| Hyprland                                            | ✅ Automatic     | Current portal restore metadata, matched through `hyprctl` or audio metadata.                                                                                           |
| Sway, other wlroots desktops, COSMIC, other portals | ❌ Unavailable   | No compositor-specific window detection. Select window audio manually.                                                                                                  |

**Key:** ✅ Works by default · ⚠️ Requires extra dependencies · ❌ Unsupported.
These statuses describe window detection. Standard portal monitor selections
can still select system audio on other desktops, and a native process hint can
identify an application. On X11, enable the native/window-PID path with
`DPE_APP_AUDIO=1`.

The portal observer uses `busctl` if installed. Desktop tools and extensions are
optional; DPE does not install or enable them. GNOME/Hyprland/Plasma portal app IDs
may require the portal to return restore metadata. Older portal versions, denied
D-Bus observation, regions, virtual outputs and ambiguous app instances can still
need manual selection. Window titles and keyboard focus are not used to guess.

To always choose audio manually on Wayland, open **DPE Settings → General** and
turn off **Automatically detect screen-share audio**. It applies to your next
share without restarting Discord, including on niri and when Discord supplies a
process hint. For a temporary override, launch with `DPE_AUDIO_AUTO_DETECT=0`.
This keeps DPE's audio routing and picker enabled.

When detection cannot identify the source, **Choose audio source** offers
application audio, system audio, or **Continue without audio**. Select **Refresh**
if an app starts playing after the picker opens. A native dialog opens if the
in-window picker is unavailable. Once the picker opens, late portal metadata does
not replace your choice.

## Requirements

You need PipeWire, a running session manager such as WirePlumber, PulseAudio
client tools and Zenity. A real PulseAudio server also works, with PipeWire
available alongside it. Your desktop needs a working `xdg-desktop-portal` backend.

Native packages declare the required audio tools. For a portable tarball, install
the packages for your distribution:

| Distribution    | Packages                                           |
| --------------- | -------------------------------------------------- |
| Arch Linux      | `pipewire`, `pipewire-audio`, `libpulse`, `zenity` |
| Debian / Ubuntu | `pipewire-bin`, `pulseaudio-utils`, `zenity`       |
| Fedora          | `pipewire-utils`, `pulseaudio-utils`, `zenity`     |

The app does not configure your audio services or change your default devices.

## Troubleshooting and limits

If an audio source cannot be identified or a required tool is missing, share
audio stays off. Check that the required audio tools are installed.
Flatpak apps on PipeWire-Pulse are matched through their running sandbox instance.
The new desktop detection paths have regression and isolated D-Bus coverage; live
Discord calls across GNOME/KDE/Hyprland and real Flatpak/Snap playback still need
desktop testing.

To try Discord's original audio routing, close Discord and run:

```sh
DPE_APP_AUDIO=0 discord-canary-prerelease-patched-electron-bin
```

Use `DPE_APP_AUDIO_TRACE=1` to enable diagnostic logs when investigating an issue.
For a tarball, replace the command with `./launch.sh`.
