# Discord patched Electron <img src="discord-patched-electron.png" align="right" width="200" alt="Discord and Tux sitting in a field with hearts between them">

Discord Canary with the latest Electron for better Linux support.

> [!WARNING]
> Experimental. Testing is limited, and bugs are expected.

## Features and patches

- Discord's native screen sharing and Krisp noise suppression.
- Wayland by default, with X11 support too.
- [Application audio](docs/wayland-audio.md) for screen sharing.
- Faster camera startup and hardware video decoding where supported.
- Support for [Vencord and Equicord](docs/client-mods.md) through their official installers.

Electron's alpha and beta builds bring Chromium's ongoing Wayland and Linux
improvements sooner. Only Discord Canary is supported because it has better
compatibility with newer Electron versions.

## Install

Download from [Releases](https://github.com/discord-patched-electron/discord-patched-electron/releases).
Each release lists the package for your distribution.

Requires an **x86-64-v3 CPU** (AVX2, such as Intel Haswell or AMD Ryzen), a recent
Linux distribution and PipeWire for screen-share audio. See the
[installation guide](docs/install.md) for setup and updates.

CI builds, checks and publishes test releases. GitHub labels these **Pre-release**
until I test and approve them as stable. Approved releases are labelled **Latest**.

## Guides

- [Launch flags, hardware acceleration and shortcuts](docs/linux-launcher.md)
- [Screen-share audio and desktop support](docs/wayland-audio.md)
- [Vencord and Equicord](docs/client-mods.md)
- [Contributing](CONTRIBUTING.md)

## AI usage

This project makes heavy use of AI, with human direction and design decisions.
I use the app daily and test changes end to end on my own setup.

## Report a bug

[Report a bug](https://github.com/discord-patched-electron/discord-patched-electron/issues)
with your version, distribution, desktop and steps to reproduce.

This package is unofficial and is not supported by or affiliated with Discord.
Report issues here rather than contacting Discord support about this package.

The automation is [MIT licensed](LICENSE). Bundled software keeps its
[own licenses](THIRD_PARTY.md).
