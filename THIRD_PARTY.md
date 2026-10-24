# Upstream sources

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

Discord client and native module binaries are proprietary. They are fetched from
Discord with locked checksums for local tests and packaging, not committed to
this repository. This project's code does not grant rights to those binaries.

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
or modify Discord's proprietary camera binary. See `docs/agent/camera-startup.md`
for the upstream algorithm and kernel API references.
