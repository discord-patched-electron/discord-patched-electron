# Matched capture benchmark runner — 2026-11-02

[`benchmark_capture_methods.py`](../../scripts/benchmark_capture_methods.py)
promotes the retained exploratory runner into a configurable, bounded tool. It
captures one owned fullscreen SDL/OpenGL window through either the host niri
ScreenCast backend or an explicit DPE game-capture bundle, then uses the real
Discord native sender and local Electron receiver. It does not launch Discord,
log in, join a voice channel, contact an SFU, or save captured images/video.

The default is a dry run: it prints the resolved options and input hashes. Add
`--run` to start one case. Use a fresh output directory for every case. The
runner owns its processes and compositor capture session; it does not stop or
reconfigure an existing application. It moves only its synthetic window onto
the specified monitor and makes that window fullscreen for matched content.

## Invocation

Compile the updated workload without opening a display:

```sh
mkdir -p out/capture-efficiency
cc -O2 -Wall -Wextra -Werror tests/screenshare/pipeline-workload.c \
  -o out/capture-efficiency/source $(pkg-config --cflags --libs sdl3 gl)
```

Pass existing verified runtime/module/library paths. The following names are
placeholders, not downloaded dependencies:

```sh
uv run python scripts/benchmark_capture_methods.py \
  --method game --game-bundle /path/to/candidate/vkcapture \
  --runtime /path/to/verified/runtime/electron \
  --voice /path/to/verified/discord_voice.node \
  --openh264 /path/to/verified/libopenh264.so \
  --workload out/capture-efficiency/source \
  --render-node /dev/dri/renderD128 --monitor DP-2 \
  --source-fps 600 --fps 60 --seconds 20 --pressure gpu \
  --output /path/to/fresh/candidate-600-gpu --run
```

For the portal comparison, use `--method portal` and omit `--game-bundle`; keep
all other workload/encoding options identical. For the old game path, pass its
retained bundle to the same command. Alternate case order across repeats, and
retain failures instead of averaging only successful runs.

`--game-handoff copy|relay` sets the candidate producer's explicit handoff mode
through `DPE_GAME_CAPTURE_HANDOFF`. It defaults to `copy`. Compare both modes
using the same newly built hook bundle so hook changes are not a confounder.

`--delivery normal|probe|fast` selects native delivery cadence explicitly. Normal
disables the production custom-source optimization for a true baseline; probe
keeps normal cadence and logs identities; fast logs and filters repeats at a
higher native polling rate. Use the same mode for both methods. Runtime evidence
must match the configured experiment or validation fails. `--gl-semaphore-sync`
enables the production GL visibility bridge in the owned game; omitting it
explicitly disables it for a comparison. `--source-display x11` exercises GLX;
Wayland/EGL remains the default. The workload's PID-qualified title identifies
its Xwayland window even when the compositor reports its proxy PID.

Options include source caps of 60, 120, 240, 600, 2000 and 4000 FPS, or `uncapped`;
actual source delivery remains measured. `--pressure none|cpu|gpu|both` reuses the
native fixture's initial/constrained/recovery phases. Capture runs are bounded
to 12–30 seconds; the source has its own 90-second limit and the runner has a
120-second overall deadline. Pressure workers have their own deadlines.

Use H.264 for matched 1080p comparisons on this AMD VCN4 machine. AV1 can be
requested with `--encoder av1-vaapi --encode-resolution 1280x720`, omitting
`--openh264`; the existing codec guard remains enabled. A 720p output is a
separate comparison and must never be counted as equal 1080p throughput.

## Evidence and limits

- `plan.json` records the exact runtime, native module, source and hook hashes.
- `native/report.json` contains real decoded-frame markers, dimensions, latency,
  adaptation and sender/receiver statistics. Native pressure evidence is also
  retained there. Distinguish actual media bitrate from bandwidth estimates.
- `workload.log` reports actual submitted FPS and final dimensions. Reject a
  comparison if the fullscreen window did not produce the intended dimensions.
- `producer-events.json` and helper logs retain source readiness and shutdown.
- `resources.json`, `resource-summary.json` and `resource-phases.json` contain
  observed CPU and DRM engine deltas, with pressure separated from native cost.
  The sampler follows children spawned from every thread, deduplicates DRM
  client IDs within each group, and distinguishes PID reuse by process start.
- `cleanup.json` and `result.json` retain child exits and overall outcome.

The portal path currently requires niri's Mutter-compatible ScreenCast API. It
measures the backend used by its portal, excluding the interactive picker. Its
small session helper uses system Python with dbus-python and PyGObject; the
main runner uses `uv run`. The source and output are fixed at 1920×1080 unless
an explicit encoder target is requested. Use a compatible monitor mode; this
runner does not change display configuration.

CPU percentages use one core as 100%. Native cost includes the local receiver
and marker readback, held constant across pairs. Compositor counters include
its unrelated session work; paired runs and quiet background conditions matter.
DRM engine percentages are process-client accounting, not whole-device power
or GPU execution timestamps. Missing counters remain missing. An isolated
high-FPS shader is not equivalent to Minecraft's game logic or GPU workload;
pressure comparisons add contention but do not establish an internet-viewer fix.

Offline tests cover non-main-thread descendants, duplicate DRM descriptors,
PID reuse and coalesced protocol replies. No hardware result is implied by
passing those tests.

For bounded rate/resize/pause checks, use
[`benchmark_capture_lifecycle.py`](../../scripts/benchmark_capture_lifecycle.py)
with the same game arguments, `--seconds 30 --fps 60 --pressure none`, and no
fixed encoder-resolution override. It records 60→30→60 producer changes,
fullscreen off/on, and a 1.2-second pause of only the owned process, with an
explicit resume before cleanup. It requires actual changed decoded dimensions,
post-change unique-frame delivery and clean producer shutdown. A non-resizable
fixture cannot satisfy its resize assertion. It does not recreate a graphics
context at unchanged dimensions.

## GSR KMS versus portal

[`benchmark_gsr_capture.py`](../../scripts/benchmark_gsr_capture.py) compares the
installed GSR executable's KMS and portal sources with the same owned fullscreen
workload, H.264 GPU encoding at 1080p60/6 Mbps, and optional pressure phases:

```sh
uv run python scripts/benchmark_gsr_capture.py \
  --method kms --workload out/capture-efficiency/source \
  --render-node /dev/dri/renderD128 --monitor DP-2 \
  --source-fps 600 --seconds 20 --pressure gpu \
  --output /path/to/fresh/gsr-kms-600-gpu --run
```

Repeat with `--method portal` and a fresh output, keeping every other argument
equal. Both methods discard encoded Matroska output to `/dev/null`; no images,
video, audio, restore tokens or desktop screenshots are saved. GSR configuration
uses a private temporary directory. The portal test exposes only the owned
compositor capture node through the existing synthetic portal fixture on a
private D-Bus with no service activation directories. It cannot open the host
portal picker. Only that fixture runs in a loopback-only network namespace;
GSR runs outside the user namespace so the installed KMS helper's file
capabilities remain usable. No capabilities or packages are installed/changed.

The installed executable observed on this machine is GSR 6.1.6-1.1, with
`/usr/bin/gsr-kms-server` already carrying `cap_sys_admin=ep`. The runner does not
automatically elevate privilege or change display modes. It requires that
existing capability on GSR's companion helper before launching a KMS run,
preventing fallback to an authorization dialog when it is absent.

This measures GSR's own capture-to-encoder resource cost and source impact. It
has no Discord sender, local decoder or frame-marker receiver. GSR's `update
fps` log counts recording-loop activity, not unique decoded frames. Neither it
nor a clean exit establishes output smoothness, image correctness or end-to-end
latency. Compare KMS against GSR portal within this experiment; do not interpret
its lower process CPU than the native sender-plus-receiver as an equivalent
Discord improvement.

The first GSR portal smoke stopped at `CreateSession` because the synthetic
portal broadcast responses and returned an object-path variant for
`session_handle`. Real portal replies target the caller, and the
[ScreenCast API](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.ScreenCast.html)
retains a string variant for that handle. The fixture now follows both rules.
The full handshake passes offline with the retained upstream GSR D-Bus client
and in a regression using a client without a broadcast Response match. This
fixture failure provides no evidence about real GSR portal capture performance.
