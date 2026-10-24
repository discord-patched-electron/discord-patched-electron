# Install

Choose a complete Canary app on the [Releases page](https://github.com/discord-patched-electron/discord-patched-electron/releases).
Builds labelled **Pre-release** are public test packages I haven't approved as stable yet.
**Latest** identifies the newest package I've tested and approved. All packages
require **x86-64-v3** (AVX2).

Download one package and `SHA256SUMS` into a new folder, then verify it:

```sh
sha256sum --check --ignore-missing SHA256SUMS
```

## Arch Linux

```sh
sudo pacman -U ./discord-canary-prerelease-patched-electron-bin-*.pkg.tar.zst
```

This works for test releases too. Install the next package with the same command.
For package-manager updates, install
[`discord-canary-prerelease-patched-electron-bin`](https://aur.archlinux.org/packages/discord-canary-prerelease-patched-electron-bin)
from the AUR. Public test builds are also available directly from Releases.

## Debian and Ubuntu

```sh
sudo apt install ./discord-canary-prerelease-patched-electron-bin-*.deb
```

## Fedora

```sh
sudo dnf install ./discord-canary-prerelease-patched-electron-bin-*.rpm
```

Packages add **Discord Canary (Patched Electron)** to your app menu.
Check the release notes for the minimum glibc version. Other RPM distributions
may need different dependencies.

## Other distributions

Extract the `.tar.gz`, open its folder and run `./launch.sh`. Keep all the files
together. The folder can be moved. Install GTK 3, NSS, ALSA, libpulse, GBM, libdrm,
CUPS, Xss, Xtst, libatomic, Python 3, Bash and coreutils through your package manager.
Screen-share audio also needs PipeWire tools, PulseAudio tools, a session manager
such as WirePlumber, and Zenity.

If the tarball fails with a sandbox error, use the native package for your
distribution where available.

## Updates

On GitHub, select **Watch → Custom → Releases** to get notifications of new builds.
Choose email or inbox delivery in your [notification settings](https://github.com/settings/notifications).
Release notes list the Discord and Electron versions, changes and test results.

Close Discord, then install the next package or replace the extracted folder.
Reinstall Vencord or Equicord afterward using its [official installer](client-mods.md).
You do not need to uninstall it first.

Settings, login sessions and cache are stored separately from stock Discord in
`~/.config/discord-canary-prerelease-patched-electron-bin` (or under `XDG_CONFIG_HOME`
if set). Updates preserve them and your [launcher settings](linux-launcher.md).

Discord client updates are checked independently of Electron. Routine Electron
updates are spaced at least four days apart, with an exception for identified
high or critical security fixes. Unchanged inputs don't produce another build.
