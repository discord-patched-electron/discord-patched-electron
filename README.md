### Discord patched Electron <img src="discord-patched-electron.png" align="right" width="200" alt="Discord and Tux sitting in a field with hearts between them">

Discord's official Canary desktop client with the latest Electron for better Linux
support. Uses the desktop client, rather than a web wrapper like Vesktop or Equibop.

> [!WARNING]
> Experimental. Testing is limited, and bugs are expected.
>
> [Using this project breaks Discord TOS](#discord-terms-of-service).

<br />

## Features and patches

### Runtime and media

- Newer Electron with native Wayland and X11 support.
- Discord’s native screensharing and Krisp noise suppression.
- [Improved stream quality, hardware encoding and video decoding](docs/screensharing.md).
- Experimental game and direct monitor capture.
- [Application audio](docs/wayland-audio.md) for screensharing.
- Faster camera startup and optional outgoing webcam mirroring.

### Desktop controls

- Native titlebars and individually configurable window buttons.
- Resizable windows without Discord’s minimum-size limit.
- Floating video popouts with optional titlebars and member lists.
- Notification flashing and optional mention-only app badges.
- Custom tray icons and minimized startup.
- Optional desktop media controls.

### Customization and updates

- OpenAsar themes and startup preferences.
- Multilingual spell checking with Chromium dictionaries.
- Graphics presets and custom Electron flags.
- Autoscroll and middle-click paste controls.
- Built-in GPU, media and WebRTC diagnostics.
- [Vencord or Equicord](docs/client-mods.md), preserved across updates.
- Automatic, verified Discord and patched Electron updates.

On first launch, the app downloads Discord's client files directly from Discord.
Downloads are verified before use.

Electron's beta builds bring Chromium's ongoing Wayland and Linux
improvements sooner. Only Discord Canary is supported because it has better
compatibility with newer Electron versions.

## Install

| Distribution        | Package                                                                                                                                                                                                                                                                                                                                                        |
| ------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Arch Linux          | [AUR](https://aur.archlinux.org/packages/discord-canary-prerelease-patched-electron-bin)                                                                                                                                                                                                                                                                       |
| Debian / Ubuntu     | [.deb](https://github.com/discord-patched-electron/discord-patched-electron/releases/latest/download/discord-canary-prerelease-patched-electron-bin-x86_64.deb)                                                                                                                                                                                                |
| Fedora              | [.rpm](https://github.com/discord-patched-electron/discord-patched-electron/releases/latest/download/discord-canary-prerelease-patched-electron-bin-x86_64.rpm)                                                                                                                                                                                                |
| Other distributions | [AppImage launcher](https://github.com/discord-patched-electron/discord-patched-electron/releases/latest/download/discord-canary-prerelease-patched-electron-bin-x86_64.AppImage), [.tar.gz](https://github.com/discord-patched-electron/discord-patched-electron/releases/latest/download/discord-canary-prerelease-patched-electron-bin-linux-x86_64.tar.gz) |

Requires an **x86-64-v3 CPU** (AVX2, such as Intel Haswell or AMD Ryzen), a recent
Linux distribution, network access to **Discord and GitHub** for downloads and
update checks, and PipeWire for screen-share audio. See the
[installation guide](docs/install.md) for setup and updates.

These links install the latest tested release.
[Pre-release builds](https://github.com/discord-patched-electron/discord-patched-electron/releases)
are available for testing.

## Settings and client mods

Open **User Settings → DPE Settings**, above **Log Out**, to configure the app.
See [settings and launcher options](docs/linux-launcher.md) for details.

First-run setup offers Vencord, Equicord or no client mod. Use **Mods** to change
your choice later; see [client mods](docs/client-mods.md).

## Guides

- [Appearance, startup and launcher settings](docs/linux-launcher.md)
- [Screen-share audio and desktop support](docs/wayland-audio.md)
- [Screensharing, game capture and direct capture](docs/screensharing.md)
- [Vencord and Equicord](docs/client-mods.md)
- [Local development and reusable dev profiles](docs/development.md)
- [Contributing](CONTRIBUTING.md)
- [Maintainer guide](docs/agent/README.md)

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

## Discord Terms of Service

Using DPE, Equicord or Vencord violates Discord’s Terms of Service, as does using
any other client modification.

Avoid plugins that enable abusive behavior. If losing access to your account
would be a serious problem, use the official, unmodified client instead.
Consider a server’s rules before posting screenshots of a modified client.

Adapted from [Vencord’s disclaimer](https://github.com/Vendicated/Vencord#disclaimer).
