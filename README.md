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
- Integrated OpenAsar for custom themes and startup settings.
- Easily configurable Electron flags, with graphics presets and custom overrides.
- [Vencord and Equicord](docs/client-mods.md) support that persists across updates.
- Automatic Discord and patched Electron updates.

On first launch, the app downloads Discord's client files directly from Discord.
Downloads are verified before use.

Electron's beta builds bring Chromium's ongoing Wayland and Linux
improvements sooner. Only Discord Canary is supported because it has better
compatibility with newer Electron versions.

## Install

| Distribution        | Package                                                                                                                                                                     |
| ------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Arch Linux          | [AUR](https://aur.archlinux.org/packages/discord-canary-prerelease-patched-electron-bin)                                                                                    |
| Debian / Ubuntu     | [.deb](https://github.com/discord-patched-electron/discord-patched-electron/releases/latest/download/discord-canary-prerelease-patched-electron-bin-x86_64.deb)             |
| Fedora              | [.rpm](https://github.com/discord-patched-electron/discord-patched-electron/releases/latest/download/discord-canary-prerelease-patched-electron-bin-x86_64.rpm)             |
| Other distributions | [.tar.gz](https://github.com/discord-patched-electron/discord-patched-electron/releases/latest/download/discord-canary-prerelease-patched-electron-bin-linux-x86_64.tar.gz) |

Requires an **x86-64-v3 CPU** (AVX2, such as Intel Haswell or AMD Ryzen), a recent
Linux distribution, network access to **Discord and GitHub** for downloads and
update checks, and PipeWire for screen-share audio. See the
[installation guide](docs/install.md) for setup and updates.

These links install the latest tested release.
[Pre-release builds](https://github.com/discord-patched-electron/discord-patched-electron/releases)
are available for testing.

## Configure OpenAsar and flags

Open Discord's **User Settings**, then click **OpenAsar (discord-patched-electron …)**
in the version information at the bottom of the left sidebar to open
**DPE OpenAsar Config**.

Use **Settings** and **Theming** to configure OpenAsar. The **Flags** tab provides
display options, graphics presets under **Advanced**, and a box for custom
Electron flags. After changing flags, choose **Save**, then **Restart** to apply
them. See [launcher settings](docs/linux-launcher.md) for more details.

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
