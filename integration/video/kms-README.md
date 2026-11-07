# KMS monitor capture

`dpe-kms-source` is the unprivileged GPU producer; `dpe-kms-server` is DPE's narrow
privileged DRM helper. Both are GPL-3.0-only. They use GPU Screen Recorder protocol 6. The Rust client/server in `native/kms/` use the reviewed DRM exporter at
upstream revision `099a55b` as a reference. Its pinned source and provenance
remain in `vendor/`; the full license is `protocols/GSR-GPL-3.0.txt`.
The reused `vkcapture-relay.*` and `vkcapture-wire.h` retain their MIT notices.

Build both binaries from the repository without opening a GPU:

```sh
uv run python scripts/build_kms_source.py --output out/kms
```

The producer requires C11, PipeWire, GBM, EGL, GLES, libdrm and libcap development
packages. The client/server also require the pinned Rust toolchain and libclang;
the server uses libdrm and system runtime libraries. Bundles ship
both executables, manifests and corresponding source; no separate GSR installation
is needed and Electron is not recompiled.

`--list` reports connected outputs without framebuffer access. **Allow direct
screen capture** in DPE settings installs the bundled server and one user's
active/local polkit grant after desktop authentication. Turning permission off
removes that user's grant and stops active helpers. Activated updates automatically
refresh existing grants when the bundled helper or Discord executable changes,
without further authentication after initial setup. The installed root verifier
accepts only the initial code tuple or exact signed, independently approved release
components. Unsigned changes require explicit administrator approval. The server
is mode 0744, so ordinary users cannot execute it directly. The root-owned
version-3 grant pins one executable
path/inode and its change metadata. Each capture/thumbnail worker also needs a
live capability gate from that executable, authenticated through `SO_PEERCRED`.
This applies to direct and systemd-hosted workers. Software able to control the
authorized Discord process is outside this boundary. The installed action has a fixed executable and default-deny
policy. Status queries and capture starts never request interactive authorization.

`--output ID --fps 60 --render-node auto` selects an inventory ID and publishes
an explicitly synchronized DMA-BUF PipeWire source. Stdin controls are `rate N`
and `stop`; stdout contains bounded JSON events. Startup checks framebuffer
access before readiness. The source snapshots the selected primary plane and
hardware cursor on the GPU without pixel download. Unsupported layouts fail
visibly. `dpe-direct-source` uses the same KMS client and sends leased DMA-BUFs directly
to Discord's GPU adapter. `dpe-kms-source` retains the PipeWire transport as a
compatibility route. Both bypass compositor capture and the portal dialog.
