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

KDE's optional `kdotool` and GNOME's optional Window Calls extension improve
window names in the picker. The dialog explains missing dependencies. They do
not enable automatic capture matching; audio selection remains manual there.

## Known limitations

Native Wayland and X11 fixtures pass with PipeWire-Pulse and with PulseAudio plus
PipeWire. Real Discord calls, GNOME/KDE sessions and Flatpak process mapping
still need testing. A missing dependency or unresolved selection leaves share
audio off rather than capturing the whole system.

For troubleshooting, close Discord and launch it with `DPE_APP_AUDIO=0` to use
upstream routing. `DPE_APP_AUDIO_TRACE=1` enables diagnostic logs.

See [implementation and test notes](agent/wayland-audio.md) for details.
