# Install

Choose a Canary runtime package on the [Releases page](https://github.com/discord-patched-electron/discord-patched-electron/releases).
Builds labelled **Pre-release** are public test packages I haven't approved as stable yet.
**Latest** identifies the newest package I've tested and approved. All packages
require **x86-64-v3** (AVX2).

First launch needs an internet connection to download Discord's client modules.
Packages include patched Electron and a pinned open-source bootstrap; they do not
include Discord's proprietary ASARs or download Discord's stock Electron runtime.

Download one package and `SHA256SUMS` into a new folder, then verify it:

```sh
sha256sum --check --ignore-missing SHA256SUMS
```

## Arch Linux

Import the [release signing key](../packaging/release-signing-key.asc) once. Its fingerprint is
`6FC3C7F7D254C749E308113D89013A5DF4E2436C`.

```sh
curl -fLO https://raw.githubusercontent.com/discord-patched-electron/discord-patched-electron/main/packaging/release-signing-key.asc
sudo pacman-key --add release-signing-key.asc
sudo pacman-key --lsign-key 6FC3C7F7D254C749E308113D89013A5DF4E2436C
```

Download the package and its matching `.sig` into the same folder, then install:

```sh
sudo pacman -U ./discord-canary-prerelease-patched-electron-bin-*.pkg.tar.zst
```

This works for test releases too. After importing the key, `pacman -U` also accepts
the package download URL directly. Install the next package with the same command.
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

Packages add **Discord Canary** to your app menu and replace the stock Canary
package if it is installed.
Check the release notes for the minimum glibc version. Other RPM distributions
may need different dependencies.

## Other distributions

Extract the `.tar.gz`, open its folder and run `./launch.sh`. Keep all the files
together. The folder can be moved. Install GTK 3, NSS, ALSA, libpulse, GBM, libdrm,
CUPS, Xss, Xtst, libatomic, Python 3 with Capstone and pyelftools, Brotli, CA
certificates, Bash and coreutils through your package manager.
Screen-share audio also needs PipeWire tools, PulseAudio tools, a session manager
such as WirePlumber, and Zenity.

If the tarball fails with a sandbox error, use the native package for your
distribution where available.

## Updates

On GitHub, select **Watch → Custom → Releases** to get notifications of new builds.
Choose email or inbox delivery in your [notification settings](https://github.com/settings/notifications).
Release notes list the Discord and Electron versions, changes and test results.

Discord modules update automatically on launch and are checked every 15 minutes
while running. Use Discord's update button to restart when an update is ready.
Downloads are verified against Discord's manifest. A failed update keeps the
previous complete client. `DPE_CLIENT_OFFLINE=1` starts an already downloaded client
without checking for updates.

For patched Electron and integration updates, close Discord, then install the
next package or replace the extracted folder.
Reinstall Vencord or Equicord afterward using its [official installer](client-mods.md).
You do not need to uninstall it first.

Settings, login sessions and cache use the standard `~/.config/discordcanary`
folder (or under `XDG_CONFIG_HOME` if set). Updates preserve them and your
[launcher settings](linux-launcher.md). Close any running Canary instance first.
Downloaded modules live in `dpe-updater/` inside that profile.

Older builds used `~/.config/discord-canary-prerelease-patched-electron-bin`.
That folder is left untouched. To keep using it, set `DISCORD_USER_DATA_DIR`
to its full path when launching.

Discord client updates are checked independently of Electron. Routine Electron
updates are spaced at least four days apart, with an exception for identified
high or critical security fixes. Unchanged inputs don't produce another build.
