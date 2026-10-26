### Discord patched Electron <img src="discord-patched-electron.png" align="right" width="200" alt="Discord and Tux sitting in a field with hearts between them">

Discord's official Canary desktop client with the latest Electron for better Linux
support. Uses the desktop client, rather than a web wrapper like Vesktop or Equibop.

> [!WARNING]
> Experimental. Testing is limited, and bugs are expected.

<br />

## Features and patches

- Updated Electron for newer Chromium features and Linux improvements.
- Discord's native screen sharing and Krisp noise suppression.
- Wayland by default, with X11 support too.
- [Application audio](docs/wayland-audio.md) for screen sharing.
- Faster camera startup and hardware video decoding where supported.
- OpenAsar included by default, with custom themes and startup settings.
- [Vencord and Equicord](docs/client-mods.md) support that persists across updates.
- Automatic Discord and patched Electron updates.

On first launch, the app downloads Discord's client files directly from Discord.
Downloads are verified before use.

Electron's beta builds bring Chromium's ongoing Wayland and Linux
improvements sooner. Only Discord Canary is supported because it has better
compatibility with newer Electron versions.

## Install

On Arch Linux, install
[`discord-canary-prerelease-patched-electron-bin`](https://aur.archlinux.org/packages/discord-canary-prerelease-patched-electron-bin)
from the AUR. For Debian, Ubuntu and Fedora, download your package from
[Releases](https://github.com/discord-patched-electron/discord-patched-electron/releases/latest).

Requires an **x86-64-v3 CPU** (AVX2, such as Intel Haswell or AMD Ryzen), a recent
Linux distribution, network access to **Discord and GitHub** for downloads and
update checks, and PipeWire for screen-share audio. See the
[installation guide](docs/install.md) for setup and updates.

Choose **Latest** for the newest tested release.
Builds labelled **Pre-release** are available for testing.

## Guides

- [Appearance, startup and launcher settings](docs/linux-launcher.md)
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
