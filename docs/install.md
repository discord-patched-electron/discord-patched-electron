# Install

On Arch Linux, install from the AUR. On Debian, Ubuntu or Fedora, download a
package from [Releases](https://github.com/discord-patched-electron/discord-patched-electron/releases/latest).
**Latest** is the newest tested release.

You need an **x86-64-v3 CPU** (AVX2, such as Intel Haswell or AMD Ryzen), a recent
Linux distribution, and internet access to **Discord and GitHub**. Release notes
list the minimum glibc version. First launch downloads Discord's client files,
so it may take longer than later launches.

## Arch Linux

Install
[`discord-canary-prerelease-patched-electron-bin`](https://aur.archlinux.org/packages/discord-canary-prerelease-patched-electron-bin)
with your AUR helper. For example, with `yay`:

```sh
yay -S discord-canary-prerelease-patched-electron-bin
```

Your AUR helper also handles package updates.

## Debian and Ubuntu

Download the `.deb` from **Latest**, then open a terminal in your downloads folder:

```sh
sudo apt install ./discord-canary-prerelease-patched-electron-bin-*.deb
```

## Fedora

Download the `.rpm` from **Latest**, then open a terminal in your downloads folder:

```sh
sudo dnf install ./discord-canary-prerelease-patched-electron-bin-*.rpm
```

## Other distributions

Download the `.tar.gz` from **Latest**, extract it, open its folder and run
`./launch.sh`. Keep all the files together; you can move the folder.

Install these dependencies through your package manager:

- GTK 3, NSS, ALSA, libpulse, GBM, libdrm, CUPS, Xss, Xtst and libatomic.
- Python **3.11 or newer**, Brotli, CA certificates, GnuPG (`gpg` and `gpgv`),
  Bash and coreutils.
- For screen-share audio, the [audio tools and services](wayland-audio.md#requirements).

If the tarball fails with a sandbox error, use your distribution's native
package where available.

## First launch

Native packages add **Discord Canary** to your app menu and replace the stock
Canary package if installed. Close any running Canary instance before launching.
The install commands above also install the required dependencies.

Your settings, login and cache use Discord's usual `~/.config/discordcanary`
folder, or the equivalent under `XDG_CONFIG_HOME`. Updates preserve them.
The app shares this profile with stock Canary, so avoid running both at once.

For Wayland screen sharing, your desktop needs a working `xdg-desktop-portal`
backend. See [screen-share audio](wayland-audio.md) for audio setup and limitations.
To add Vencord or Equicord, follow the [mod guide](client-mods.md).

## Updates

New versions reach automatic updates after testing. The app checks for Discord
and Electron updates on launch and every 15 minutes. When an update is ready,
use Discord's update button to restart.
Downloads are verified before use; a failed update keeps the working version.
Automatic updates need no administrator access and preserve your installed mod.

For launcher, bootstrap and Linux integration changes, close Discord and install
the next package. Tarball users should extract the new version into a fresh folder.
Mods installed in the [default location](client-mods.md) are preserved.

Discord's hosted web interface updates independently of these desktop releases.

To hear about new builds, select **Watch → Custom → Releases** on GitHub and
choose your delivery method in [notification settings](https://github.com/settings/notifications).

## Startup problems

The startup screen explains failures and offers **Retry now**. Connection errors
retry after ten seconds; **Pause retries** stops the countdown. Use **Copy details**
when reporting a problem. Your desktop's close-window shortcut cancels startup.

- **Python or Brotli is missing:** install it through your package manager, then
  retry. Python is `python` on Arch and `python3` on Debian/Ubuntu and Fedora.
  Brotli is `brotli` on all three.
- **Bundled update libraries are missing:** reinstall the complete package or
  extract the whole tarball again. Keep its `python/` folder beside `launch.sh`.
  You don't need to install Python modules yourself.
- **Downloads cannot connect:** allow HTTPS access to Discord and GitHub,
  including `github.com`, `api.github.com`, `raw.githubusercontent.com` and GitHub's
  release download hosts. A first installation needs network access; an existing
  verified installation can keep working when update checks fail.

For appearance, custom profiles, offline mode and graphics troubleshooting,
see [launcher settings](linux-launcher.md).

## Verify a download

To check a package downloaded from GitHub, put it and the release's `SHA256SUMS`
in a new folder, then run:

```sh
sha256sum --check --ignore-missing SHA256SUMS
```

AUR installations check the download automatically.

## Maintainer and testing installs

Builds labelled **Pre-release** on
[Releases](https://github.com/discord-patched-electron/discord-patched-electron/releases)
are for testing. Download the package for your distribution and install it using
the commands above. Installing a test package selects that build's Discord version.

### Arch: install a release package directly

For testing a build before it reaches the AUR, import the
[release signing key](../packaging/release-signing-key.asc) once:

```sh
curl -fLO https://raw.githubusercontent.com/discord-patched-electron/discord-patched-electron/main/packaging/release-signing-key.asc
sudo pacman-key --add release-signing-key.asc
sudo pacman-key --lsign-key 6FC3C7F7D254C749E308113D89013A5DF4E2436C
```

The key fingerprint is `6FC3C7F7D254C749E308113D89013A5DF4E2436C`.
Download the `.pkg.tar.zst`, its matching `.sig` and `SHA256SUMS` into a new folder.
Check the download and install it:

```sh
sha256sum --check --ignore-missing SHA256SUMS
sudo pacman -U ./discord-canary-prerelease-patched-electron-bin-*.pkg.tar.zst
```

Use the same steps for later test builds. See the
[release guide](agent/releases.md#test-and-approve) for testing and approval.
