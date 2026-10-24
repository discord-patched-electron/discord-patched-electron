# Discord patched Electron

Discord Canary with newer Electron and better Linux support.

> [!WARNING]
> Experimental. Testing is limited, and bugs are expected.

## Features and patches

- Discord's native screen sharing and Krisp noise suppression.
- Wayland by default, with X11 support too.
- [Application audio](docs/wayland-audio.md) for screen sharing.
- Faster camera startup and hardware video decoding where supported.
- Support for [Vencord and Equicord](docs/client-mods.md) through their official installers.

**Prerelease Electron** brings newer Chromium versions sooner, while Wayland and
other Linux improvements are ongoing. **Only Discord Canary is supported**: it's
what I use daily, and Discord ships Electron upgrades there first.

## Install

Get a package from [Releases](https://github.com/discord-patched-electron/discord-patched-electron/releases):
`.pkg.tar.zst` for Arch, `.deb` for Debian/Ubuntu, `.rpm` for Fedora, or `.tar.gz`
for other distributions. Arch users can also install
[`discord-canary-prerelease-patched-electron-bin`](https://aur.archlinux.org/packages/discord-canary-prerelease-patched-electron-bin)
from the AUR.

Requires an **x86-64-v3 CPU** (AVX2, such as Intel Haswell or AMD Ryzen), a recent
Linux distribution and PipeWire for screen-share audio. See the
[installation guide](docs/install.md) for setup and updates.

CI builds, checks and publishes packages. **Pre-release** means I haven't approved
the build as stable yet; **Latest** marks an approved release. Both use prerelease Electron.

## Guides

- [Launch flags, hardware acceleration and shortcuts](docs/linux-launcher.md)
- [Screen-share audio and desktop support](docs/wayland-audio.md)
- [Vencord and Equicord](docs/client-mods.md)
- [Contributing](CONTRIBUTING.md)

## AI usage

This project makes heavy use of AI, with human direction and design decisions.
I use the app daily and test changes end to end on my own setup. Wider testing is limited.

[Report a bug](https://github.com/discord-patched-electron/discord-patched-electron/issues)
with your version, distribution, desktop and steps to reproduce.

Unofficial; not affiliated with Discord. The automation is [MIT licensed](LICENSE);
bundled software keeps its [own licenses](THIRD_PARTY.md).
