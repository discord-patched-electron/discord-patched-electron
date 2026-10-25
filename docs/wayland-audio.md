# Screen-share audio

Share a window in Discord and enable audio. On supported niri versions, the app
automatically matches the shared window to its audio. On other desktops, a
**Screen-share audio** dialog lets you choose an application, the whole system
(excluding Discord), or no audio.

Audio is selected per application. Sharing one Zen window includes audio from
its other windows and tabs too. Mixed browser audio cannot be separated by window.

You need PipeWire, its command-line audio tools, a session manager such as
WirePlumber, PulseAudio client tools and Zenity. This also works with a real
PulseAudio server, as long as PipeWire is available. The package doesn't change
your audio services or default devices.

Native packages declare the audio-tool dependencies: `pipewire`, `pipewire-audio`
and `libpulse` on Arch; `pipewire-bin` and `pulseaudio-utils` on Debian/Ubuntu;
`pipewire-utils` and `pulseaudio-utils` on Fedora. Zenity is `zenity` on all three.
For a portable tarball, install these yourself. Your desktop must also have a
working `xdg-desktop-portal` backend and a running PipeWire session manager.

KDE's optional `kdotool` and GNOME's optional Window Calls extension improve
window names in the picker. The dialog explains missing dependencies. They do
not enable automatic capture matching. Audio selection remains manual there.

## Known limitations

Live Discord calls, full GNOME/KDE sessions and audio from Flatpak apps still
need testing. If the app cannot identify the selected audio source or a required
tool is missing, it leaves share audio off.

For troubleshooting, close Discord and launch it with `DPE_APP_AUDIO=0` to use
upstream routing. `DPE_APP_AUDIO_TRACE=1` enables diagnostic logs.
