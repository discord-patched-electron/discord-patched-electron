# Cursor composition for optimized game capture

Game hooks export the rendered game image, which does not necessarily include an
OS cursor. OBS draws its cursor separately. DPE previously discarded the stock
protocol’s X11 window ID and had no corresponding composition step.

The bundled hook now preserves the exact upstream X11 window ID in unused texture
packet bytes; packet size and existing peers remain compatible. The stock OBS
protocol path also preserves its existing window ID. X11/XWayland sources with a
window ID use `cursor-composition` GPU-copy handoff instead of relay. The producer
alpha-blends the bounded cursor sprite into its owned GPU output before releasing
the source frame. It does not read back the game image or depend on Discord’s
metadata/cursor compositor. Sources without an X11 window ID retain their normal
handoff.

Cursor queries use a private XCB connection and asynchronous reply polling, with
one query batch outstanding and a 100-ms deadline. Replies are bounded to 256×256,
positions are relative to the exact captured window, and mismatched window/frame
dimensions suppress the sprite rather than guessing scale. Transparent/blank
cursor images stay transparent; out-of-window positions are suppressed. The
sprite texture is uploaded only when XFixes changes its cursor serial. Runtime
XCB/XFixes libraries are dynamically loaded, so an unavailable X11 display or
library suppresses cursor composition without crashing capture. Build headers
are supplied by the existing XCB/XFixes development dependencies.

Native Wayland applications with no X11 surface remain a limitation: a global
Wayland OS cursor is not exposed through the game texture protocol. Game-drawn
cursors remain present. Use monitor capture when an OS cursor is required for
such applications. This change does not create a fake pointer or claim to expose
a locked pointer independently of the game’s own cursor image.

Validation: `out/game-cursor/cursor-check /dev/dri/renderD128` used an entirely
synthetic 16×16 GPU target and a two-pixel red cursor. Pixel assertions verified
BGRA conversion, hotspot/position and hidden-sprite behavior against a blue
background. No desktop, game or camera pixels were recorded. Nineteen capture
backend tests and the new geometry/payload regression passed. The complete
patched hook bundle, corresponding source, licenses and native helpers were
rebuilt with warnings as errors at `out/game-cursor/bundle`.

Actual game cursor appearance and native Wayland cursor capture were not tested.
Games must restart to load the updated bundled hook; stock hooks already carry
the window ID. Picker thumbnails currently retain the image-only preview path.

## Broker environment and output-coordinate correction

A later real report still showed direct relay because the broker’s restricted
child environment omitted `DISPLAY` and `XAUTHORITY`. The hook and producer were
current, but the producer deliberately disabled X11 cursor support without its
display. Both display credentials now pass from the trusted Discord environment
to its child. Game hook/loader variables remain excluded. A regression covers
this boundary.

A private-Xvfb synthetic cursor check exercised the actual DPE hook protocol,
broker, GPU producer and Discord H.264 encode/decode receiver. It additionally
exposed inverted cursor Y coordinates: exported DMA-BUF rows are interpreted
top-to-bottom, unlike a displayed GL framebuffer. The GPU overlay now uses that
storage orientation, with an asymmetric red/green sprite regression.

`out/cursor-pipeline/result.json` records 17 decoded cursor-bearing frames with
the expected rectangle (100,80)–(115,95), rather than the old inverted Y=84.
`native-final/report.json` confirms native encoding/decoding and capture paths,
but the full delivery/timing gate failed because only 18 receiver callbacks
arrived despite 171 encoded frames. That unrelated fixture/transport symptom was
reported to the quality investigation and is not presented as a complete native
performance pass. Only synthetic pixels were used; the user pointer, real game,
account and live processes were not changed.

The broker-environment regression and complete backend suite passed (20 tests).
The asymmetric GPU cursor test passed on the AMD render node. Updated producer
and preview binaries are in `out/cursor-pipeline/helpers`; hook libraries are
unchanged from the complete cursor bundle. Native Wayland limitations remain.
