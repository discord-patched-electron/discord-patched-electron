# KMS monitor capture

`dpe-kms-source` is the unprivileged GPU producer; `dpe-kms-server` is DPE's narrow
privileged DRM helper. Both are GPL-3.0-only. They use GPU Screen Recorder protocol
6 and its reviewed DRM exporter at upstream revision `099a55b`. The pinned source
and provenance are in `vendor/`; the full license is `protocols/GSR-GPL-3.0.txt`.
The reused `vkcapture-relay.*` and `vkcapture-wire.h` retain their MIT notices.

Build both binaries from the repository without opening a GPU:

```sh
uv run python scripts/build_kms_source.py --output out/kms
```

The producer requires C11, PipeWire, GBM, EGL, GLES, libdrm and libcap development
packages. The privileged server links only libdrm and the C library. Bundles ship
both executables, manifests and corresponding source; no separate GSR installation
is needed and Electron is not recompiled.

`--list` reports connected outputs without framebuffer access. **Allow direct
screen capture** in DPE settings installs the bundled server and one user's
active/local polkit grant after desktop authentication. Turning permission off
removes that user's grant and stops active helpers. Other software running as
the same Unix user can invoke the permitted helper; it is not an Electron-only
security boundary. The installed action has a fixed executable and default-deny
policy. Status queries and capture starts never request interactive authorization.

`--output ID --fps 60 --render-node auto` selects an inventory ID and publishes
an explicitly synchronized DMA-BUF PipeWire source. Stdin controls are `rate N`
and `stop`; stdout contains bounded JSON events. Startup checks framebuffer
access before readiness. The source snapshots the selected primary plane and
hardware cursor on the GPU without pixel download. Unsupported layouts fail
visibly. PipeWire remains the transport into Discord; compositor capture and the
portal dialog are bypassed.
