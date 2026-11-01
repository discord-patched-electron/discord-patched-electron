# Third-party software and sources

This project's code is [MIT licensed](LICENSE). Bundled components keep their
own licenses and notices. Discord's executable and module files download separately from Discord;
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

The external OpenH264 decoder downloads directly from Cisco into each user's
profile. Its version and SHA-256 checksums are pinned in
[`integration/updater/openh264.json`](integration/updater/openh264.json).
The codec binary and compressed download are excluded from release packages.
The [complete Cisco binary license](integration/updater/OPENH264-BINARY-LICENSE.txt)
is also available beside the OpenH264 controls in Settings.

## Discord client files

Discord client and native module binaries are proprietary. They are fetched from
Discord with verified checksums for local tests or into the user's profile at
runtime. They are not committed to this repository or included in release
packages. This project's code does not grant rights to those binaries.
Compatibility patchers contain small Discord JavaScript excerpts as match
patterns for modifying downloaded modules; the package is not entirely free of
Discord source excerpts.

The application icon, `packaging/discord-canary.png`, is Discord's Canary branding,
not covered by this project's MIT license. It is the unmodified 256×256
`files/discord.png` from the [official Canary 1.0.2381 archive](https://canary.dl2.discordapp.net/distro/app/canary/linux/x64/1.0.2381/full.distro).
Archive SHA-256: `e8129d0a369ee632e36521fbae2629476a0c0d186a0347880f39fc6d5d5a466d`;
icon SHA-256: `8e056efd24189d2713bc91564e4a90ccff7f056d65ea56b50ad2dd62112865b9`.

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

## UI components

The settings interface and audio search field use [Preact](https://preactjs.com/),
pinned in `bun.lock` and bundled locally without a CDN dependency. Its MIT license
ships beside the runtime as `integration/discord-ui/PREACT-LICENSE.txt`.
Fallback icons use [Bootstrap Icons](https://github.com/twbs/icons/tree/v1.13.1)
under the MIT license in `integration/discord-ui/BOOTSTRAP-ICONS-LICENSE.txt`.
The generic download arrow is an original project drawing on the same icon grid.
Discord's own icons are resolved from the running client and are not redistributed.

## Tray icon presets

The **Vencord** preset contains unmodified PNG artwork from
[Vesktop](https://github.com/Vencord/Vesktop/tree/68b3d5b36d2efc88ffd05909f2e9f24823021c90/static/tray),
revision `68b3d5b36d2efc88ffd05909f2e9f24823021c90`, by Vendicated and Vesktop
contributors. Vesktop supplies default and unread icons; its default icon is
also used for voice states without a separate upstream variant.

The **Equicord** preset contains unmodified PNG artwork from
[Equibop](https://github.com/Equicord/Equibop/tree/57406f627221bc6054deb9834c18fa9cd914d96a/static/tray),
revision `57406f627221bc6054deb9834c18fa9cd914d96a`, by Equicord/Equibop
contributors. Equibop is derived from Vesktop, with the original Vendicated and
Vesktop contributor notices retained. Its default, unread, connected/idle,
speaking, muted and deafened icons are included.

Both upstream projects use **GPL-3.0-or-later**; these assets retain that license,
not this project's MIT license. The complete
[GNU GPL version 3](integration/updater/assets/tray-presets/GPL-3.0.txt) ships with
the presets. The [asset manifest](integration/updater/assets/tray-presets/manifest.json)
records the upstream revisions, source paths, original image hashes and source
archive links/checksums. Images are bundled unchanged and scaled only for display.
Vencord, Equicord, Vesktop and Equibop names and logos belong to their respective
projects; their inclusion does not imply endorsement.

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
