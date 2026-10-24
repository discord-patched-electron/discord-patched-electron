# Discord patched Electron

Discord's desktop app on a newer Electron, with Linux fixes for screen sharing,
application audio and camera startup. It keeps Discord's native streaming
integration and Krisp.

This is a **CI-driven build and packaging project**. It carries a small patch
queue, assembles pinned upstream sources and produces ready-to-run Linux packages.
You don't need to compile Electron to use it.

Discord **Stable, PTB and Canary** each get a separate package, all using
prerelease Electron. Wayland is the main target; X11 is supported too. Builds
require an **x86-64-v3** CPU (AVX2, such as Intel Haswell or AMD Ryzen) and a
recent Linux distribution. Screen-share audio needs PipeWire.

## Install

Choose a complete app build from [Releases](https://github.com/discord-patched-electron/discord-patched-electron/releases),
then follow the [installation guide](docs/install.md):

| Format    | Intended use                                                |
| --------- | ----------------------------------------------------------- |
| `.deb`    | Debian and Ubuntu                                           |
| `.rpm`    | Fedora; other RPM distributions may need dependency changes |
| `.tar.gz` | Other distributions and portable installations              |

A release labelled **runtime cache** contains only Electron, not Discord.
Test packages are available as artifacts on successful
[release workflow runs](https://github.com/discord-patched-electron/discord-patched-electron/actions/workflows/release.yml).
Public app releases currently require maintainer promotion. AUR recipes are
included, but automated AUR publication is not enabled yet.

**These are test builds.** CI checks launch and relaunch, native video/Krisp,
isolated audio routing and package installation. Live Discord calls and full
GNOME/KDE sessions still need testing. See [known limitations](docs/wayland-audio.md).

Each channel uses its own profile and can coexist with stock Discord.

## Configure

Put launch flags in `~/.config/discord-canary-patched-electron.conf`, one per line.
Replace `canary` with `stable` or `ptb` for those channels. For example:

```text
# Use Xwayland instead of native Wayland.
--ozone-platform=x11
```

- [Launcher settings, hardware acceleration and shortcuts](docs/linux-launcher.md)
- [Screen-share audio and desktop support](docs/wayland-audio.md)
- [Equicord's official installer](docs/equicord.md)

## How updates work

CI checks Discord's shipped app, modules and Electron binary every 15 minutes.
Discord-only changes are tested against the last verified runtime without waiting
for a new Electron build. Discord's web interface updates independently; the
packaged ASAR and native modules arrive through package updates.

Routine Electron prereleases are grouped into a **four-day update interval**.
Applicable high or critical security advisories can bypass that delay. A changed
Discord runtime binary triggers compatibility checks; commits to its source fork
alone don't trigger compilation. Content hashes skip unchanged work, and the local
compiler retains its build objects.

Compilation runs on the project's local runner. GitHub-hosted jobs handle tests
and packaging. The [release workflow](docs/agent/releases.md) reuses a verified
runtime to produce all package formats and publishes only after its checks pass.
Client qualification and public release promotion are separate steps today.

## Contribute

[CONTRIBUTING.md](CONTRIBUTING.md) covers setup with Bun and uv, checks, the
repository layout and the release process. Most changes need no Electron build.

Report bugs through [GitHub issues](https://github.com/discord-patched-electron/discord-patched-electron/issues).
Include the package version, desktop environment, display session and steps to
reproduce. Implementation history and test evidence live in [agent docs](docs/agent/README.md).

This project is unofficial and is not affiliated with Discord.
The automation is [MIT licensed](LICENSE); bundled software keeps its own licenses.
See [third-party credits](THIRD_PARTY.md).
