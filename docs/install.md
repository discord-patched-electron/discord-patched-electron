# Install

Choose your Discord channel on the [Releases page](https://github.com/discord-patched-electron/discord-patched-electron/releases).
Use a complete app release, not a **runtime cache** release. All builds use
prerelease Electron and require **x86-64-v3** (AVX2).

## Debian and Ubuntu

```sh
sudo apt install ./discord-canary-prerelease-patched-electron-bin-*.deb
```

## Fedora

```sh
sudo dnf install ./discord-canary-prerelease-patched-electron-bin-*.rpm
```

Packages install under `/opt/` and add a desktop entry. Launch **Discord Canary
(Patched Electron)** from your menu. Stable and PTB have separate packages.
Current builds need glibc 2.38 or newer (for example, Ubuntu 24.04 or Debian 13).
Each release manifest records the minimum required by its actual binaries.
Other RPM distributions may use different dependency names; packagers can adjust
[the package metadata](../scripts/package_release.py).

## Other distributions

Extract the `.tar.gz`, open its folder and run `./launch.sh`. Keep all the files
together. The folder can be moved. Install GTK 3, NSS, ALSA, libpulse, GBM, libdrm,
CUPS, Xss, Xtst, libatomic, Python 3, Bash and coreutils through your package manager.
Screen-share audio also needs PipeWire tools, PulseAudio tools, a session manager
such as WirePlumber, and Zenity.

The tarball uses Chromium's user-namespace sandbox. If your distribution restricts
user namespaces for downloaded apps, use its native package or configure a scoped
AppArmor rule; don't disable the sandbox. Debian packages include that rule.

Download `SHA256SUMS` alongside the packages, then run
`sha256sum --check --ignore-missing SHA256SUMS` to verify the files you downloaded.
Checksums also live inside the tarball for packagers.

## Updates

Install the next package or replace the extracted folder. Your profile and
[launcher settings](linux-launcher.md) stay in your config directory.
Discord's web interface updates itself, but the packaged ASAR and native modules
update with these packages. Don't copy modules from a stock Discord installation.

Arch recipes remain available in the repository; automated AUR publication still
needs a publishing key. See [release packaging](agent/releases.md) for building
packages from a verified runtime without recompiling Electron.
