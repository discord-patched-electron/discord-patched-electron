# Discord patched Electron

Discord Canary with a newer Electron and Linux fixes. CI builds and tests the
packages, publishes them for testing, then promotes them after maintainer approval.

We use **prerelease Electron** to bring newer Chromium versions to Linux users as
soon as practical. Wayland, screen sharing and other Linux improvements are still
ongoing, and this lets us pick them up sooner.

**Canary is the only supported Discord channel for now.** It's the channel the
maintainer uses and where Discord tries Electron upgrades first. Following it
keeps compatibility work focused. Stable and PTB are disabled.

## Features and patches

- Discord's native screen sharing and Krisp noise suppression.
- Native Wayland by default, with X11 support too.
- [Application audio](docs/wayland-audio.md) for screen sharing, with automatic selection on niri.
- Camera startup fixes.
- Internal capture devices hidden from Discord's audio-device picker.
- Hardware video decoding where supported, with [configurable launch flags](docs/linux-launcher.md).
- A separate profile, alongside stock Discord.
- Support for [Vencord and Equicord](docs/client-mods.md) through their official installers.

## Installation

Builds require an **x86-64-v3** CPU (AVX2, such as Intel Haswell or AMD Ryzen),
a recent Linux distribution and PipeWire for screen-share audio.

Download a complete Canary package from
[Releases](https://github.com/discord-patched-electron/discord-patched-electron/releases).
**Pre-release** means the package is public and ready for testing, but hasn't been
approved by the maintainer yet. Approved packages still use prerelease Electron.

| Format         | Distribution        |
| -------------- | ------------------- |
| `.pkg.tar.zst` | Arch Linux          |
| `.deb`         | Debian and Ubuntu   |
| `.rpm`         | Fedora              |
| `.tar.gz`      | Other distributions |

Follow the [installation guide](docs/install.md) for setup and updates.
AUR publishing is not enabled yet; Arch users can install the release package
with `pacman -U`.

Live calls and full GNOME/KDE sessions still need verification.
See [desktop support and known limitations](docs/wayland-audio.md).

## Documentation

- [Installation and updates](docs/install.md)
- [Launch flags, hardware acceleration and shortcuts](docs/linux-launcher.md)
- [Screen-share audio](docs/wayland-audio.md)
- [Vencord and Equicord](docs/client-mods.md)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, checks and pull requests.
Most changes don't need an Electron build.

[Report a bug](https://github.com/discord-patched-electron/discord-patched-electron/issues)
with your package version, distribution, desktop environment and steps to reproduce.

This project is unofficial and is not affiliated with Discord.
The automation is [MIT licensed](LICENSE); bundled software keeps its own licenses.
See [third-party credits](THIRD_PARTY.md).
