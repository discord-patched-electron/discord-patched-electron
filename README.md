# Discord patched Electron

Discord's desktop app on a newer Electron, with Linux fixes for screen sharing,
application audio and camera startup. It keeps Discord's native streaming and Krisp.

Supports Discord **Stable, PTB and Canary**, all using prerelease Electron.
Wayland is the main target; X11 is supported too. Requires an **x86-64-v3** CPU
(AVX2: Intel Haswell, AMD Ryzen or newer) and PipeWire for screen-share audio.

## Install

This is still a test build. See [Releases](https://github.com/discord-patched-electron/discord-patched-electron/releases)
for available downloads and [installation](docs/install.md) for package formats.
A release labelled **runtime cache** contains only Electron, not a runnable Discord app.
AUR publication is not enabled yet.

The app uses its own profile, so it can coexist with stock Discord.
Live calls and GNOME/KDE sessions still need testing; see the
[known limitations](docs/wayland-audio.md).

## Configure

Put launch flags in `~/.config/discord-canary-patched-electron.conf`, one per line.
Replace `canary` with `stable` or `ptb` for those channels. For example:

```text
# Use Xwayland instead of native Wayland.
--ozone-platform=x11
```

- [Launcher settings and hardware acceleration](docs/linux-launcher.md)
- [Screen-share audio](docs/wayland-audio.md)
- [Optional Equicord installation](docs/equicord.md)

## Contribute

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and checks. Build history,
implementation notes and test evidence live in [agent docs](docs/agent/README.md).

This project is unofficial and is not affiliated with Discord.
The automation is [MIT licensed](LICENSE); bundled software keeps its own licenses.
See [third-party credits](THIRD_PARTY.md).
