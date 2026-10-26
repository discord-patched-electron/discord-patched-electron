# Screen-share audio

On Wayland, share a window in Discord and enable audio. On supported niri versions,
the app automatically selects that window's application audio. On other desktops, a
**Screen-share audio** dialog lets you choose an application, the whole system
(excluding Discord), or no audio.

Sharing an application includes audio from all its windows and tabs. For example,
sharing one Zen window also shares audio from its other windows. Browser audio
cannot be separated by tab or window.

For X11, enable the adapter with `DPE_APP_AUDIO=1` when launching. See
[launcher settings](linux-launcher.md#troubleshooting) for how to set it.

## Requirements

You need PipeWire, a running session manager such as WirePlumber, PulseAudio
client tools and Zenity. A real PulseAudio server also works, with PipeWire
available alongside it. Your desktop needs a working `xdg-desktop-portal` backend.

Native packages declare the required audio tools. For a portable tarball, install
the packages for your distribution:

- **Arch:** `pipewire`, `pipewire-audio`, `libpulse` and `zenity`.
- **Debian/Ubuntu:** `pipewire-bin`, `pulseaudio-utils` and `zenity`.
- **Fedora:** `pipewire-utils`, `pulseaudio-utils` and `zenity`.

The app does not configure your audio services or change your default devices.
KDE's optional `kdotool` and GNOME's optional Window Calls extension improve window
names in the picker. Audio selection remains manual on those desktops.

## Troubleshooting and limits

If an audio source cannot be identified or a required tool is missing, share
audio stays off. The picker explains missing dependencies.
Live Discord calls, full GNOME/KDE sessions and audio from Flatpak apps still
need testing.

To try Discord's original audio routing, close Discord and run:

```sh
DPE_APP_AUDIO=0 discord-canary-prerelease-patched-electron-bin
```

Use `DPE_APP_AUDIO_TRACE=1` to enable diagnostic logs when investigating an issue.
For a tarball, replace the command with `./launch.sh`.
