# Selectable capture backends, 2026-11-02

This pass completes the direct Wayland and opt-in game-capture producers and
connects them to the separate DPE Screensharing settings window. It supersedes
the earlier notes describing these as unconnected prototypes. Automatic, portal
and X11 choices remain available where their native paths support them.

## Selection and ownership

`integration/video/capture-picker.c` bridges the reviewed native picker ABI.
`scripts/capture_picker_scope.py` verifies the whole native module and derives
the show/new/destroy linkage slots. Installation checks each resolved target, or
its exact own-module lazy PLT stub, before changing those slots. Unknown native
revisions retain the original picker. An asynchronous N-API callback transfers
requests to the renderer; helper processes never call JavaScript from a capture
thread.

`integration/video/capture-backends.cjs` reads the saved method and forwards
custom requests over restricted IPC. `integration/updater/capture-backends.cjs`
owns the source list and helpers. Only a trusted Discord main frame can request
capture; only the separate settings window can choose a listed source. Source
identities and game titles stay in the chooser and are excluded from ordinary
capture diagnostics. No shell command or helper path comes from renderer IPC.

The chooser starts only the selected source. Its PipeWire node and same-user
socket are passed through the native callback, preserving Discord's subsequent
source, encoder and transport setup. The native bridge transfers the socket FD
exactly once when the capturer is created and tracks the corresponding destroy
event. Old capture events cannot terminate a newer source. Cancelling, closing
the chooser, owner destruction, navigation and process failure stop the owned
helper. Process-group cleanup includes children surviving their group leader.
An unconsumed selection is cleared under Discord's own selected-config mutex,
only when its FD and node match, before closing the bridge-owned descriptor.
Discord's portal release function alone does not clear that selected config.

Startup errors stay visible for retry; rejected game textures suggest SDR or
the System picker. Selection alone never claims an active capture backend:
diagnostics require a native capture owner and observed PipeWire frames. Capture
rate changes are forwarded to the active producer, including changes after the
initial source selection.

## Backend boundaries

- **Direct Wayland:** `ext-image-copy-capture-v1`, output-source and linux-dmabuf
  protocols; normally oriented monitors. GPU buffers use an explicitly shared
  format/modifier on the compositor's actual device. Unsupported protocols,
  transforms or import paths are unavailable or fail explicitly. GNOME normally
  uses the portal because it does not offer this direct protocol.
- **Vulkan / OpenGL:** bundled, patched obs-vkcapture hooks activated for one
  explicitly launched game. No global layer registration or attachment to an
  already running application. The hook transfers a bounded frame lease after
  GPU completion; the consumer acknowledges it after its GPU copy. Exporter UUID
  and actual import determine the GPU instead of assuming `renderD128`. RGB8 SDR
  is supported; FP16/HDR and incompatible device/modifier combinations fail
  explicitly. The game itself is never terminated by capture cleanup.

The implementation does not establish support for sandboxed game launchers,
32-bit games, HDR conversion, rotated direct outputs or X11 game-capture routing.
The general portal path remains appropriate for those sources. The game wrapper
also rejects paths containing loader separators, which cannot be represented
safely in `LD_PRELOAD`.

## Shipped assets

`scripts/stage_capture_backends.py` builds the small helpers from pinned sources,
or verifies a reusable bundle. Packaging includes the capture bundle and exact
patched GPL hook source and licenses. The runtime uses
`capture/dpe-wayland-source` and `capture/vkcapture/` under `DPE_APP_ROOT`.
This does not require rebuilding Electron. Native scope scripts and sidecars
participate in updater compatibility keys so older cached adapters are not
silently reused.

## Evidence and limits

Artifacts are under `/mnt/storage/dpe-capture-backends-20261102/`:

- `wayland-direct-proof/report.json`: actual niri output to a private PipeWire
  DMA-BUF consumer, bounded run, no saved pixels.
- `vkcapture-smoke/report.json` and `vkcapture-vulkan-rgb8/report.json`: opted-in
  owned OpenGL and Vulkan sources through GPU handoff/import/copy to a private
  PipeWire consumer. The Vulkan FP16 rejection was also exercised.
- `native-picker-final/report.json`: production native picker bridge selecting
  a synthetic private DMA-BUF node, followed by AMD AV1 encoding and Electron
  decoding at 1280×720; request, started and stopped events all observed.
  An initial selected-but-unconsumed source is cancelled before the second pick,
  exercising the real native selected-config mutex and descriptor cleanup.
- `native-picker-current/report.json`: the same cancellation and hardware
  encode/decode proof with the newer reviewed Discord native module, using its
  production HRD/dimensions corrections. Both native revisions passed.
- `sync-corrected/report.json`: delayed explicit GPU readiness, corrected native
  absolute deadline, actual AV1 encode/decode; see the
  [synchronization investigation](screenshare-sync-20261102.md).
- `settings-final/settings.json`: actual Electron settings UI and restricted
  IPC, keyboard selection, disabled/loading/empty/error states, saved custom
  method, cancellation, themes and narrow/zoomed layout.
- `candidate-smoke/launch-report.json`: packaged candidate reaches the signed-out
  Discord login form on native Wayland, with pinned client inputs and no uncaught
  exceptions. The bounded check closes its own processes afterward.

The final repository checks passed: `bun run lint`, 628 Python tests and
334 JavaScript tests. The runnable candidate's
`candidate/launch-test.sh` fixes a separate signed-out profile and pinned inputs;
it does not reuse the user's active Discord profile. The candidate verification
report records runtime, module, helper, UI and bundle hashes.

These checks establish the producers and native handoff separately. They do not
claim a single combined direct/game-producer-to-remote-Discord-viewer run, an
NVENC/Intel hardware test, or reproduction of the original sustained Minecraft
bitrate collapse. The earlier high-FPS observations remain in
[the Minecraft investigation](screenshare-minecraft-20261102.md).

## No-viewer diagnostics

The reported screenshot had no viewer. Negotiated AV1 is not proof that an
encoder has started. DPE now reports **Not started** only when native statistics
explicitly show zero encoded frames and zero output FPS with no encoder name.
Unknown or missing counters remain unknown. An actual hardware/software label
still requires the reported encoder implementation; the UI does not infer it
from the codec preference. Discord's own stream panel can likewise remain blank
until it has outgoing encoder statistics.
