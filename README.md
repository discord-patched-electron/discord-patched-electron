# Discord patched Electron

Discord's desktop app with a newer Electron and Linux fixes. CI builds and tests
packages for Discord Stable, PTB and Canary, all using prerelease Electron.

## Features and patches

- Discord's native screen sharing and Krisp noise suppression.
- Native Wayland by default, with X11 support too.
- [Application audio](docs/wayland-audio.md) for screen sharing, with automatic selection on niri.
- Camera startup fixes.
- Internal capture devices hidden from Discord's audio-device picker.
- Hardware video decoding where supported, with [configurable launch flags](docs/linux-launcher.md).
- Separate profiles for Stable, PTB and Canary, alongside stock Discord.
- Support for [Vencord and Equicord](docs/client-mods.md) through their official installers.

## Installation

Builds require an **x86-64-v3** CPU (AVX2, such as Intel Haswell or AMD Ryzen),
a recent Linux distribution and PipeWire for screen-share audio.

Download a package for your Discord channel from
[Releases](https://github.com/discord-patched-electron/discord-patched-electron/releases)
or the artifacts of a successful
[package build](https://github.com/discord-patched-electron/discord-patched-electron/actions/workflows/release.yml).
A **runtime cache** download contains only Electron; choose a complete app package.

| Format    | Distribution        |
| --------- | ------------------- |
| `.deb`    | Debian and Ubuntu   |
| `.rpm`    | Fedora              |
| `.tar.gz` | Other distributions |

Follow the [installation guide](docs/install.md) for setup and updates.
AUR packages are not published yet.

**These are test builds.** Live calls and full GNOME/KDE sessions still need
verification. See [desktop support and known limitations](docs/wayland-audio.md).

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
