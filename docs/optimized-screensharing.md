# Optimized screensharing

See [screensharing features and quality settings](screensharing.md) for codecs,
automatic AV1 cropping, bitrate behavior and incoming decoder diagnostics.

Open **DPE Settings → Screensharing** and enable **Experimental capture methods**.
In Discord’s screenshare picker, open **Optimized** to choose an available screen
or game. Use the regular system picker when an optimized source is unavailable.

## Games and applications

Start DPE once to register its stable launcher. Copy the command from **Game
capture** in DPE Settings into Steam’s launch options. It contains your full
launcher path followed by `-- %command%`. Steam replaces `%command%` with the game.

For another launcher, replace `%command%` with the game’s executable and arguments.
Do not run `%command%` or `game-command` literally in a terminal. The settings
command uses an absolute path and does not need shell expansion.

Restart games that were already running. The wrapper resolves the current DPE
bundle after updates and enables Vulkan/OpenGL capture for that process. Games
launched with stock `obs-gamecapture` hooks are also supported. DPE avoids adding
a second set of hooks when those hooks are already enabled. Compatibility depends
on the game, graphics API and driver. The launcher does not need direct screen
capture permission.

For Vulkan-only capture, prefix the copied launcher command with
`env OBS_VKCAPTURE=1`. DPE uses its bundled Vulkan layer and skips the OpenGL
preload. This is a capture selection, not an anti-cheat compatibility flag.

To use installed stock OBS hooks instead, prefix it with `env OBS_COMPAT=1`.
This requires `obs-gamecapture`. Combine both flags for stock Vulkan-only capture
(which requires the installed OBS Vulkan layer). Stock hooks share OBS’s receiver
endpoint, so OBS and DPE cannot own it simultaneously.

OS cursors are composed into X11/XWayland game shares on the GPU. Native Wayland
games without an X11 surface still need monitor capture to include an OS cursor.
Restart games after updating DPE to load new capture hooks.

## Direct screen capture

Enable **Allow direct screen capture** and approve the desktop authorization
prompt to install the system rule. Direct capture is insecure compared to PipeWire
because it bypasses the desktop’s permission picker. The rule applies to DPE’s helper, which other applications running as your
user may also invoke.

**System permission installed** means the rule exists. A separate error explains
when the rule is obsolete or the launch environment blocks its use. Use **Update
permission** for an older rule. Enabling experimental capture with an older rule
also requests its update. A blocked environment may require restarting DPE outside
that restriction.

Turning off direct capture removes the rule after desktop authorization. Turning
off experimental capture removes it too when one exists. Cancelling authorization
or a removal failure restores the toggle. The status line shows progress.

## Encoding

Discord documents H.264 and VP8, plus platform-specific HEVC and AV1 support.
DPE negotiates only encoders supported by the native module and hardware.
[Discord’s streaming overview](https://discord.com/blog/how-it-all-goes-live-an-overview-of-discords-streaming-technology)
explains codec selection.

**Excluded codecs** prevents selected codecs from encoding your next screenshare.
Discord negotiates a supported remaining codec. Camera calls and incoming video
keep their normal codecs. If no usable encoder remains, allow another codec.

**Current screenshare** shows capture and encoding details when available. A
negotiated codec alone does not prove an encoder has started, especially with no
viewers.
