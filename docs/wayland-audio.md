# Screen-share audio

On Wayland, share a window in Discord and enable audio.

| Desktop                 | Audio selection                                                                                                  |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------- |
| Supported niri versions | Automatically selects the shared window's application.                                                           |
| Other Wayland desktops  | A **Share audio** dialog inside Discord offers application audio, system audio (excluding Discord), or no audio. |
| X11                     | Enable application audio with `DPE_APP_AUDIO=1`. See [launcher settings](linux-launcher.md#troubleshooting).     |

Sharing an application includes audio from all its windows and tabs. For example,
sharing one Zen window also shares audio from its other windows. Browser audio
cannot be separated by tab or window.

On Plasma, the audio dialog is expected for both screen and window sharing.
Choose **System audio** to include other applications, or search for one app.
If an app starts playing after the dialog opens, select **Refresh**.
**Continue without audio** keeps your video share running. A native dialog opens
if the in-window picker is unavailable.

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
KDE's optional `kdotool` and GNOME's optional Window Calls extension improve window
names in the picker. Audio selection remains manual on those desktops.

## Troubleshooting and limits

If an audio source cannot be identified or a required tool is missing, share
audio stays off. Check that the required audio tools are installed.
Flatpak apps on PipeWire-Pulse are matched through their running sandbox instance.
Live Discord calls, full GNOME/KDE sessions, and real Flatpak/Snap playback still
need desktop testing.

To try Discord's original audio routing, close Discord and run:

```sh
DPE_APP_AUDIO=0 discord-canary-prerelease-patched-electron-bin
```

Use `DPE_APP_AUDIO_TRACE=1` to enable diagnostic logs when investigating an issue.
For a tarball, replace the command with `./launch.sh`.
