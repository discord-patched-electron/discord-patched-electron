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
CUPS, Xss, Xtst, libatomic, Python 3.11 or newer, Brotli, CA
certificates, GnuPG (`gpg` and `gpgv`), Bash and coreutils through your package manager.
Screen-share audio also needs PipeWire tools, PulseAudio tools, a session manager
such as WirePlumber, and Zenity.

If the tarball fails with a sandbox error, use the native package for your
distribution where available.

## Dependencies and startup errors

The native packages declare their system dependencies. Installing with the
`pacman -U`, `apt install` or `dnf install` commands above resolves them through
your distribution's repositories. The AUR package uses the same Arch dependency
list. A portable `.tar.gz` cannot install dependencies; use the list above.

The updater needs system Python **3.11 or newer**, Brotli, CA certificates,
GnuPG (`gpg` and `gpgv`), Bash and coreutils. Capstone and pyelftools, including
Capstone's native library, are bundled. Running the app does not require pip,
uv, Bun, a separate Node.js installation or a compiler.

- **Python or Brotli is missing:** install the named tool through your package
  manager, then choose **Retry now**. Python is packaged as `python` on Arch and
  `python3` on Debian/Ubuntu and Fedora; Brotli is `brotli` on all three.
- **Bundled update libraries are missing:** reinstall the complete native package
  or extract the entire tarball again. Keep its `python/` folder beside
  `launch.sh`; installing Python modules globally is unnecessary.

For Wayland screen sharing, your desktop also needs a working
`xdg-desktop-portal` backend. [Screen-share audio](wayland-audio.md) needs a running
PipeWire session manager such as WirePlumber. Packages include or declare the
audio tools, but do not configure your desktop's services.

## Updates

On GitHub, select **Watch → Custom → Releases** to get notifications of new builds.
Choose email or inbox delivery in your [notification settings](https://github.com/settings/notifications).
Release notes list the Discord and Electron versions, changes and test results.

Discord's desktop ASAR, native modules and patched Electron update only after
manual release approval. Checks run on launch and every 15 minutes while running.
Use Discord's update button to restart when an approved update is ready.
Module downloads come from Discord at the exact versions and checksums in the
signed release; they never follow Discord's live latest manifest. This also covers
module changes that keep the same Discord version number.
Downloads and compatibility checks finish before activation. A failed download
keeps the current approved installation. Explicitly installing a test package
uses its pinned candidate modules. Discord's hosted web interface remains served
by Discord and is outside this desktop release approval.
The startup screen follows Discord's layout with one progress bar. If startup
fails, it shows recovery steps and **Retry now**. Connection failures retry after
10 seconds; **Pause retries** stops the countdown. **Copy details** copies the
error for troubleshooting. Your desktop's close-window shortcut quits and cancels
preparation.
OpenAsar's settings and CSS editor are included. Splash Theming is enabled by default and uses Discord or custom
theme colors cached from your client; Quickstart uses a prepared installation and checks updates in the background.

Electron updates live in your profile and need no administrator access. Your
installed mod loader stays in place. For bootstrap and Linux integration updates,
close Discord, then install the next package or replace the extracted folder.
Mods installed through the [default installer location](client-mods.md) are preserved
when the underlying bootstrap is replaced.

Settings, login sessions and cache use the standard `~/.config/discordcanary`
folder (or under `XDG_CONFIG_HOME` if set). Updates preserve them and your
[launcher settings](linux-launcher.md). Close any running Canary instance first.
The fork shares Discord's `app-<version>/`, versioned module directories and
`installer.db`. Replaced stock files are backed up under `dpe-updater/backups/`.
Preparation caches remain under `dpe-updater/` and `dpe-runtime/`.
The stock updater can replace a shared installation; this package's launcher
restores the fork from verified sources on its next launch.
`DISCORD_USER_DATA_DIR` changes the parent folder (Canary adds `discordcanary/`);
`DPE_PROFILE_DIR` selects an exact profile path. `DPE_CLIENT_OFFLINE=1` skips both update checks.
`DPE_RUNTIME_UPDATES=0` skips only Electron update checks while retaining an already updated
runtime; `DPE_RUNTIME_BUNDLED=1` uses the runtime installed with the package.
Offline mode and Quickstart also require package-pinned or approved modules.
If an older updater cached an unapproved client, launch online once to repair it.

Older builds used `~/.config/discord-canary-prerelease-patched-electron-bin`.
That folder is left untouched. To keep using it, set `DPE_PROFILE_DIR`
to its full path when launching.

Discord module releases can reuse the existing Electron build. Routine Electron
builds are spaced at least four days apart, with an exception for identified
high or critical security fixes. Both require manual approval before automatic
delivery. Unchanged inputs don't produce another build.
