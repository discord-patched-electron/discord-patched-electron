# Third-party software and sources

This project's code is [MIT licensed](LICENSE). Bundled components keep their
own licenses and notices. Discord's client files download separately from Discord;
they are not included in release packages.

## Electron and packaging

`packaging/upstream/` is an unmodified snapshot of the AUR
`discord_arch_electron` package at the commit recorded in `provenance.json`.
Its author/contributor comments are retained. The launcher and resource/bootstrap
patches in this project derive from those files. The Krisp patcher is executed
from that snapshot, with validation around it in `scripts/patch_krisp.py`.

`patches/discord/44/integration.patch` is the exact binary-capable Git diff
between the upstream base and Discord fork commit in `config/sources.lock.json`.
It includes public Electron/Chromium/Node/FFmpeg changes under their respective
upstream licenses. The Electron license is retained alongside the patch. The
assembled distribution must retain all its upstream licenses and notices.

The current runtime uses FFmpeg's LGPL build, dav1d for software AV1 and libvpx
for software VP8/VP9; their notices are in `LICENSES.chromium.html`. HEVC playback
uses platform hardware decoding. No software HEVC decoder or system-libde265
adapter is included. Open-source licences and codec patent rights are separate;
see the [codec licensing investigation](docs/agent/codec-support.md#software-hevc-and-licensing-review)
before changing or redistributing codec components.

## Discord client files

Discord client and native module binaries are proprietary. They are fetched from
Discord with verified checksums for local tests or into the user's profile at
runtime. They are not committed to this repository or included in release
packages. This project's code does not grant rights to those binaries.
Compatibility patchers contain small Discord JavaScript excerpts as match
patterns for modifying downloaded modules; the package is not entirely free of
Discord source excerpts.

## OpenAsar and startup assets

The replacement bootstrap uses [OpenAsar](https://github.com/GooseMod/OpenAsar),
under AGPL-3.0. Its source commit and archive checksum are pinned in
`config/openasar.lock.json`. Packages include its complete `src/` tree and license
under `bootstrap/`, alongside this project's readable integration source.
The project supplies the updater and the small `resources/app.asar` entry point.

The unmodified MIT-licensed `splash/index.html` and `config.html` from
[OpenAsar/cdn](https://github.com/OpenAsar/cdn) are retained with their license and
pinned revision in `integration/updater/openasar/`. The active splash uses this
project's `modern-splash.html` with OpenAsar's original preload from the bundled
bootstrap source. Discord's animation and gg sans fonts are excluded from
packages: they are fetched from Discord's CDN into the user's cache on launch
and checked against the hashes and sizes in `integration/updater/splash-media.json`.
The pinned CDN archive also supplies the settings screen's MIT-licensed Monaco
editor. Its license and third-party notices ship under `bootstrap/cdn/monaco-editor/`.
The original settings page loads Roboto through Google Fonts when available.

## Python dependencies

The updater bundles Capstone and pyelftools from the wheels pinned in `uv.lock`.
Their Python sources, Capstone's native library and upstream license notices
are retained under `python/`, including each wheel's `.dist-info` directory.

## Test fixtures and Linux helpers

`tests/audio/speech.wav` is synthesized test speech generated with eSpeak NG
1.52.0, then resampled with FFmpeg. Its phrase and generation metadata are recorded
in `tests/audio/speech.json`. It contains no recorded personal audio. The native
frame test downloads Discord's public header using its pinned URL and checksum;
that header retains its upstream license and is not vendored here.

The Wayland fixture builds a pinned [niri](https://github.com/niri-wm/niri)
revision under GPL-3.0-or-later. The test-only executable is cached in CI and is
not included in the Discord runtime or AUR packages. Its exact source revision
and build flags are recorded in `scripts/build_test_compositor.sh` and the
fixture artifacts. The distro supplies PipeWire, PulseAudio and Zenity tools.
Vesktop/venmic served as routing and source-selection references; their code or
native addons are not bundled by this audio adapter.

`integration/camera/v4l2-probe.c` is project-owned MIT-licensed compatibility
code, using Linux's public V4L2 userspace API. It does not include WebRTC source
or modify Discord's proprietary camera binary. See [camera startup](https://github.com/discord-patched-electron/discord-patched-electron/blob/main/docs/agent/camera-startup.md)
for the upstream algorithm and kernel API references.
