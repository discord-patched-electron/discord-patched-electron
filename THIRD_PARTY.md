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
