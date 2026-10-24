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
Other RPM distributions may need different dependencies.

## Other distributions

Extract the `.tar.gz`, open its folder and run `./launch.sh`. Keep all the files
together. The folder can be moved. Install GTK 3, NSS, ALSA, libpulse, GBM, libdrm,
CUPS, Xss, Xtst, libatomic, Python 3, Bash and coreutils through your package manager.
Screen-share audio also needs PipeWire tools, PulseAudio tools, a session manager
such as WirePlumber, and Zenity.

If the tarball fails with a sandbox error, use the native package for your
distribution where available.

Download `SHA256SUMS` alongside the packages, then run
`sha256sum --check --ignore-missing SHA256SUMS` to verify the files you downloaded.

## Updates

Install the next package or replace the extracted folder. Your profile and
[launcher settings](linux-launcher.md) stay in your config directory.
If you use Vencord or Equicord, follow the [mod update instructions](client-mods.md)
before replacing the app.

AUR packages are not published yet.
