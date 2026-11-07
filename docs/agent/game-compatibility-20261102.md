# Game capture compatibility and picker previews

The ordinary `obs-gamecapture game-command` and `OBS_VKCAPTURE=1` launch
methods can supply games to DPE without installing different hooks. The broker
understands upstream's packed 128-byte hello/texture and 32-byte control records,
handles partial stream reads, checks peer credentials and descriptor bounds, and
passes selected textures through the existing GPU copy producer. It refuses
unhandled HDR color spaces instead of silently treating them as SDR.

Upstream uses one Linux abstract socket, `/com/obsproject/vkcapture`. DPE attempts
to bind it only while its picker requests game discovery. If OBS already owns it,
DPE reports `compatibility: "in-use"`; it never replaces the socket or attaches
to an OBS-owned game. Closing the picker releases idle upstream connections and
the listener; a seven-second discovery timeout is a second cleanup path. A live
screenshare owns its separate accepted connection and continues after that
listener closes. Upstream cannot give OBS and DPE simultaneous independent
ownership of the same unmodified hook. Bundled DPE hooks use a separate socket.
Occupied stock-hook sources and other unavailable game sources are omitted from
the Optimized picker. Main-process logs record the reason only when it changes,
without source titles or process details. Independently available bundled hooks
remain selectable; normal refresh restores stock sources when ownership clears.

Upstream textures have implicit DMA-BUF synchronization and no frame-ready,
release, or producer-rate messages. Consequently that path reports
`handoff: "copy"`, `handoffFallback: "upstream-implicit-sync"` and
`producerPacing: false`. DPE limits its own copy rate, but cannot reduce vanilla
hook copies at the game's presentation rate. The bundled hooks retain the paced
producer and explicit ownership/fence protocol used for the faster path.

`~/.local/bin/dpe-game-capture -- game-command` is a stable delegating command.
Normal DPE launch registers the current bundle in
`$XDG_DATA_HOME/discord-patched-electron/game-capture.json` (with the normal
`~/.local/share` fallback). The command resolves that record at invocation, so
it does not embed a version directory. It preserves arguments and loader
variables, leaves unrelated commands alone, and can fall back to an installed
`obs-gamecapture` if the DPE bundle is unavailable. Existing vanilla hooks are
not loaded a second time.

Game thumbnails use the separate `dpe-vkcapture-preview` process. The broker
accepts `{op:"preview",token,width,height}` only for an idle source, caps it at
480×270, and returns `{width,height,format:"BGRA",pixels:<base64>}`. EGL scales
on the GPU before reading only the thumbnail. There is no PipeWire stream,
encoder, saved image or full-resolution CPU readback. The child has a bounded
lifetime. Cancellation closes its hook connection; reconnection retains the
source token, and a real share waits briefly for that handshake instead of
using a half-retired preview allocation. No preview interrupts an active share.

Primary references reviewed:

- [Upstream usage and launch modes](https://github.com/nowrep/obs-vkcapture#usage)
- [Wire structures](https://github.com/nowrep/obs-vkcapture/blob/master/src/capture.h)
- [Hook connection, control polling and texture export](https://github.com/nowrep/obs-vkcapture/blob/master/src/capture.c)
- [OBS receiver and import fallbacks](https://github.com/nowrep/obs-vkcapture/blob/master/src/vkcapture.c)
- [Vanilla launch script](https://github.com/nowrep/obs-vkcapture/blob/master/src/obs-gamecapture.in)

Final validation passed 16 focused tests, including framing, descriptors, an
already-owned OBS listener, truthful copy selection, preview/reconnect token
ownership, and updating the stable launcher without replacing unrelated user
commands. The complete game bundle built without rebuilding Electron.

A bounded synthetic EGL window using the unmodified pinned upstream hook
passed discovery, GPU thumbnail generation, reconnect and actual native
screenshare delivery. The existing isolated native receiver encoded with AMD
H.264 and decoded in software for six seconds; every capture, codec, delivery,
timing, scaler/sync, picker-lifecycle and transport gate passed. The thumbnail
fit the requested 160×90 bound at 83×90 because niri tiled the source to
945×1018. Pixels stayed in memory. The broker exited cleanly and owned test
processes were stopped. This was an isolated native transport test, not a call
through Discord's servers. Evidence is in `out/picker-refinement/game-tests.log`,
`game-build.log`, `game-gpu-result.json` and `vanilla-native/report.json`.

An earlier ad hoc PipeWire sink rejected format negotiation; the reused native
receiver passed against the same production source, so that sink is not used
as validation evidence.
