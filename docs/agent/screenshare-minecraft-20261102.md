# Minecraft high-FPS capture investigation

The user's actual `oneclient-1.8.9-ornithe-multimc` instance was tested on
2 November 2026. The severe sustained viewer/bitrate collapse did **not**
reproduce in these local native sender/receiver checks, including the repeated
pair immediately after the user confirmed being in the affected scene.
The overall issue remains open. These results do not establish a Discord
server/viewer result or a controlled performance improvement.

## Actual application and scope

Read-only instance metadata and relevant mod bytecode identify Minecraft 1.8.9,
Fabric 0.19.5, Argentum 1.0.2, Celeritas 2.5.0-pre.1, and Pylon 0.2.4. Pylon
replaces the nominal LWJGL display with SDL OpenGL context and swap calls; the
LWJGL version alone does not identify the window-system backend. Saved options
have VSync off, the vanilla unlimited FPS setting, render distance 14,
`pauseOnLostFocus:false`, and a render-ahead limit of three. No option was edited.
The earlier FO 26.3 preparation was not launched and is not this reproduction.

The user reports 2,000–4,000 FPS. That rate was not instrumented in the game.
EvergreenHUD contains frame-time measurements, but no external exporter was
established and no agent was attached to the Java process. Read-only evidence is
in `out/minecraft-research-20261102/oneclient-readonly.json` and adjacent bytecode
extracts. Account data, process command-line tokens, worlds and server logs were
not used.

The checks captured only the identified Minecraft window using niri's Mutter
screencast interface and the existing isolated native PipeWire fixture. The
actual window was 1900×1018; requested quality was 1080p60 with a 6 Mbps maximum,
AMD VA-API H.264 and an Electron receiver. Each test lasted 20 seconds and used
a new test profile and private network namespace. Production transport options
and real native feedback were enabled, but no Discord SFU or account was involved.
The fixture's `source_fps:240` records the compositor test setup, not measured
Minecraft FPS. No extra CPU/GPU pressure was added to the game runs.

No pixels or encoded packets were saved. Embedded-marker analysis was disabled
for real game content, so decoded counts do not establish unique-content frame
rate or end-to-end latency. Briefly focusing the game did not change its settings.
Owned capture sessions and test processes stopped after each run; the game
remained running. All four run directories retain cleanup markers and no owned
test process remains alive.

## Results

Artifacts are under `/mnt/storage/dpe-minecraft-20261102/`. The first pair,
`window-current` and `window-original`, preceded confirmation of the affected
scene; it also showed no sustained collapse. After the user replied “now i am”,
the original and corrected paths were repeated in that order:

| Confirmed-scene run                      | Original             | Corrected candidate                        |
| ---------------------------------------- | -------------------- | ------------------------------------------ |
| Artifact directory                       | `confirmed-original` | `confirmed-current`                        |
| Captured DMA-BUF frames                  | 4,787                | 1,153                                      |
| Capture callbacks                        | 4,787                | 1,156                                      |
| Negotiated producer limit                | No added preference  | 60 FPS                                     |
| Encoded/decoded dimensions               | 1920×1024            | 1900×1018                                  |
| Final decoded frame count                | 1,195                | 1,169                                      |
| Sampled encoder FPS                      | 60–61                | 44 and 52 for two samples; otherwise 60–61 |
| Final rate-limiter discards              | 0                    | 26                                         |
| Encoder queue/congestion-window discards | 0 / 0                | 0 / 0                                      |
| Connection bandwidth estimate            | 10 Mbps throughout   | 10 Mbps throughout                         |
| Capture return errors / receiver freezes | 0 / 0                | 0 / 0                                      |

Native logs establish GPU DMA-BUF import and hardware encoding; every sampled
encoder is `amd: vaapi` and every sampled decoder is `electron`. The candidate
preserves the actual visible dimensions and successfully negotiates the initial
60 FPS producer preference. Its three downstream pacing skips are separate from
the 26 later WebRTC rate-limiter discards. The original module retains its
64-pixel rounding. Neither run triggers CPU/quality adaptation or codec fallback.

The candidate's initial actual bitrate of roughly 0.84–2.34 Mbps coexists with
normal frame delivery and low quantization values. The configured allocation
already reaches 6 Mbps; actual output alone is not a bandwidth-limit signal.
Later, a 7.70 Mbps burst precedes the brief cadence dip. Native
`encoder_bitrate_adjuster` records utilization 1.24626 and changes allocation
from 6 Mbps to approximately 4.952 Mbps, then 5.814 Mbps, then back to 6 Mbps.
The receiver delivers 42 and 51 frames in the affected intervals, then returns
to approximately 60 FPS by second 11. This is a real temporary cadence drop,
consistent with the logged overshoot-control response, rather than evidence
of the reported sustained bandwidth collapse.

The two runs contain different live gameplay and different encoded geometry.
They are not a matched quality, CPU/GPU usage or latency comparison. The local
feedback ceiling is not an internet bandwidth measurement. No new production
rate-control override is justified: disabling the dropper or forcing a bitrate
would obscure this evidence and could worsen congestion. A focused follow-up
could use a repeatable low-detail to high-detail transition at fixed geometry;
the original user symptom still needs a reproducible server/viewer observation.

`comparison.json` preserves all four runs and their samples; `comparison.html`
visualizes only the confirmed-scene pair. Original native SHA-256 is
`2352578d855e5ff75c0433fe8ca0ffa6d599691a506a7908da3d0d6523cc91b8`;
candidate native SHA-256 is
`72096efc87a407b975e9913477d387e2de9a1f1028575c1f97a22365bacb007c`.
The helper remains `8df8f9658a16…`; the installed Discord application was not
replaced. The [current runnable candidate](capture-implementation-20261102.md#feedback-audit-diagnostic-meanings-and-bounded-network-recovery)
is unchanged by this test-only continuation.

## Optional OpenGL submission fixture

`tests/screenshare/pressure.c` also has a standalone `gl` mode for frequent
desktop-OpenGL draws and flushes. Unlike the existing long compute dispatch,
it can exercise submission overhead. It is not Minecraft, Java, a swapchain,
or compositor presentation, and is not automatically added to game tests.

```sh
cc -std=c11 -O2 -Wall -Wextra -Werror tests/screenshare/pressure.c \
  $(pkg-config --cflags --libs gbm egl glesv2) -o out/screenshare-gl-pressure
out/screenshare-gl-pressure gl 2 /dev/dri/renderD128 240 64
```

The optional cap is `240` or `uncapped`; draw count is 1–512. Duration is at
most 15 seconds, with an independent process alarm, a bounded drain and at
most two outstanding GPU fences. The 720p framebuffer, buffers and textures
are reused. `fence_observed_us_*` measures CPU-observed fence retirement,
including polling and cap sleep; it is not GPU execution latency.

A two-second capped API smoke passed on the RX 7900 GRE: all 473 submitted
batches completed and at most one fence was pending. That retained smoke log
uses the earlier `completion_us_*` label; the source now uses the explicit
`fence_observed_us_*` name. No claim of an uncapped game-equivalent rate follows.
Offline compile/argument tests reject invalid work limits before device access.
Independent native and codec reviews found no justified production change.

Final validation passed `bun run lint` and `bun run test` (616 Python and
317 JavaScript tests). Logs are `lint-final.log` and `tests-final.log` under
the artifact root. The scope and limitations above remain separate from those
offline check results; no new settings UI or runtime packaging was needed.
