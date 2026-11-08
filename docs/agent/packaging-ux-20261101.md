# Packaging review and repository distribution

## Recommendation

Keep native packages as the default on Debian/Ubuntu and Fedora. Add a small,
project-owned HTTPS APT/RPM repository for their bootstrap updates as a separate
publication step after manual release approval. This can reuse the already tested
packages; it needs no Electron compilation or Blacksmith runner. No repository
has been deployed by this review.

The November 1 release inspected here has roughly 96 KB Debian and 108 KB RPM
installers, a 7 MB application archive and a separate shared Electron runtime.
Hosting a few bootstrap versions is inexpensive. The app already updates its
application, client and runtime in the user profile; a distro repository fills
the remaining gap for the loader, trust key, dependency metadata and desktop
integration. Installing a local `.deb` or `.rpm` does **not** subscribe the user
to future package-manager updates.

## APT and RPM publication design

- Use existing `aptly`/`reprepro` and `createrepo_c`, not hand-written package
  metadata. Publish only a manually approved release with verified signatures,
  and keep an explicitly separate opt-in testing repository.
- APT needs `Packages` indexes and a signed `InRelease`, an immutable versioned
  package pool, by-hash index support and atomic snapshot publication. Use a
  scoped keyring under `/etc/apt/keyrings` and a deb822 `.sources` entry with
  `Signed-By`, not `apt-key`. Pin the repository to DPE package names.
- DNF needs a `.repo` file, signed RPMs and signed repository metadata. Keep
  `gpgcheck=1` and `repo_gpgcheck=1`. The existing release signature covers the
  checksum list and the Arch package; it is **not** an RPM signature. Sign RPMs
  before computing release checksums, so repository publication never changes
  already released bytes. Test key import and upgrades with an isolated RPM DB.
- Put versioned filenames in the package pool: the current fixed GitHub asset
  names are appropriate for release URLs but cannot share one repository path
  across versions. Retain old pool files and by-hash metadata during upgrades.
- Serialize publishers, reject older versions, verify the approval again before
  swapping the snapshot, and test installation/upgrading with fresh APT/DNF
  caches. Treat a key rotation as an upgrade path, not a replacement download.
- A small static HTTPS host is sufficient. GitHub Pages is a possible starting
  point for bootstrap packages and metadata, subject to its limits; keep the
  large Electron payloads on the existing release cache. Do not deploy a service
  or advertise an install URL until signed install/upgrade tests pass.

Sources: [Debian third-party repository guidance](https://wiki.debian.org/DebianRepository/UseThirdParty),
[Debian archive format](https://wiki.debian.org/DebianRepository/Format),
[APT repository publishing with aptly](https://www.aptly.info/doc/aptly/publish/),
[createrepo_c](https://github.com/rpm-software-management/createrepo_c),
[RPM signing](https://rpm.org/docs/6.1.x/man/rpmsign.1),
[GitHub Pages limits](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits).

A Launchpad PPA is not a direct upload destination for the existing `.deb`: it
accepts source packages and builds them for Ubuntu series. COPR also needs a
source RPM/spec or another supported build source and must meet its hosting
policy. Either could build a small bootstrap recipe without compiling Electron,
but both add recipe maintenance; a PPA also does not cover Debian. A project-owned
binary repository is the most direct fit for the current qualification pipeline.

Sources: [Launchpad PPAs](https://ubuntu.com/docs/launchpad/user/PPA/),
[COPR user documentation and policy](https://docs.copr.fedorainfracloud.org/user_documentation.html).

## AppImage dependency boundary

Historical November 1 design. The [November 5 dependency review](appimage-host-libraries-20261105.md)
supersedes the desktop/audio bundling described below.

The user requested dependency bundling, not an embedded Electron/application
snapshot. The revised AppImage contains the unchanged bootstrap plus standalone
Python, GnuPG, Brotli, Zenity, PipeWire/PulseAudio client tools and their library
dependencies. Electron, Discord, application.zip and Cisco's OpenH264 still come
from the existing updater and keep their normal verification/update lifecycle.
No runtime compilation is involved.

Dependencies are collected by checksum-pinned linuxdeploy inside a digest-pinned
Ubuntu 22.04 container, with a checksum-pinned standalone Python distribution.
Signed Ubuntu package metadata supplies the distro libraries; exact installed
versions and copyright/source notices are included in the AppImage. Dependency
changes require a new AppImage. The bundler excludes libc and driver/display ABI
libraries to avoid replacing the host graphics stack. The host still supplies
PipeWire services, desktop portals, graphics drivers and a compatible libc.

Audio subprocesses resolve the bundled helper directory only in AppImage mode;
native installs retain their system paths. Relaunch/autostart and cache identity
use the persistent AppImage filename, not its temporary mount. Do not disable
Chromium's sandbox to make the AppImage work: Ubuntu's user-namespace restrictions
can still require a scoped host policy, which the native Debian package supplies.

Tests must execute the produced image as an unprivileged user on a minimal distro
without Python/GPG/Brotli/GTK/NSS installed and with network disabled. Separately,
launch the real application with verified cached archives and a fresh profile,
then relaunch it. FUSE mounting and cross-distro GUI behavior are distinct from
extraction-mode qualification; do not claim universal portability.

Sources: [AppImage packaging](https://docs.appimage.org/packaging-guide/manual.html),
[linuxdeploy](https://github.com/linuxdeploy/linuxdeploy),
[standalone Python distributions](https://gregoryszorc.com/docs/python-build-standalone/main/distributions.html),
[Electron AppImage sandboxing](https://docs.appimage.org/user-guide/troubleshooting/electron-sandboxing.html).

## PR #9 findings addressed

- Removed silent omission when the builder is missing; packaging requires it.
- Pinned both appimagetool and the type-2 runtime by checksum. The runtime's
  upstream URL is rolling; changed bytes fail closed and require a reviewed pin
  update. appimagetool receives the local runtime instead of fetching one itself.
- The independent workflow builds this checkout and executes its AppRun with
  spaces, URL arguments and a nonzero exit status. It has read-only permissions,
  pinned actions, no shell interpolation of dispatch input and no release writes.
- The normal release workflow builds, checks, launches and uploads AppImage;
  release asset verification and install notes accept it while preserving old
  four-format releases. The signed checksum list covers the new asset.
- AppImage metadata lives outside the unchanged bootstrap payload. Channel IDs
  are derived for Canary, PTB and Stable, with the correct icon and desktop entry.
- Relaunch/autostart point to the persistent AppImage, not its temporary mount.
  The bootstrap cache uses the same stable identity across mounts, preserving
  downloads and update state on successive launches.

Local validation results are recorded in the PR handoff. FUSE mount behavior and
fresh distro dependency resolution remain distinct from extraction-mode tests.

## Validation

- Lint and 795 unit tests passed (541 Python, 254 JavaScript).
- A real AppImage built by the pinned builder/runtime passed an offline,
  unprivileged Ubuntu 22.04 container probe without host Python/GPG/Brotli/GTK/NSS.
  The probe checks bundled executable selection, SSL trust, compression, library
  loading, argument forwarding and exit status.
- Wrapped a signature-verified published bootstrap/application fixture with the
  revised loader and dependency bundle; verified unchanged inner payload bytes
  and absence of Electron, ASARs and client modules in the image.
- Real Electron/Discord launch and relaunch passed with a fresh profile under
  X11/Xvfb, using that fixture's hash-verified application/runtime archives.
  This tests packaging against an existing qualified application; the changed
  audio helper selection is covered by unit tests and the PR's source CI checks.
- Real FUSE mounting and fresh Fedora/Ubuntu desktop sessions have not been
  exercised locally. CI retains the AppImage launch reports. No account login,
  messages or calls were performed, and no Electron compilation was started.

The real GUI check exposed a bundled-readline conflict with the host shell.
The final recipe separates GUI libraries from helper-only dependencies; helpers
use linuxdeploy's relative ELF paths. The corrected image passed relaunch.
