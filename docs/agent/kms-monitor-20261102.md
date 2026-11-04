# KMS monitor capture, 2 November 2026

This pass implements the monitor path deferred in the earlier capture-efficiency
work. The user requested implementation guided by source and ownership reasoning,
followed by short final checks; another comparative benchmark sweep is out of scope.

## Design and integration

The standalone `dpe-kms-source` uses GPU Screen Recorder's protocol 6 to request
scanout descriptors from an already installed `/usr/bin/gsr-kms-server`. Discovery
reads DRM metadata without starting that privileged process. Capture starts only
after explicit monitor selection. DPE never changes file capabilities. When
needed, **Authorize and share** invokes the installed helper through polkit and
uses the desktop's authentication agent. The helper connects through a private socket, is checked
against the spawned PID and expected UID (root only for explicit authorization),
then switches to a sequenced-packet socket.
Descriptor counts, wire layout and response sizes are checked before use.

The GPU snapshots the selected primary plane and premultiplied hardware cursor
into DPE-owned linear BGRA buffers. It does not read pixels back to the CPU.
Explicit acquire/release timelines protect these output buffers through Discord's
GPU consumer; an output is reusable only after actual GPU release. A bounded pool
and at most one pending KMS request prevent a growing capture queue. GSR's source
protocol does not provide an immutable scanout lease: copying promptly follows the
same limitation as GSR, and does not prove an atomic snapshot across plane changes.

PipeWire remains the transport into Discord's native capturer. This bypasses the
compositor capture implementation and portal dialog, not PipeWire itself. There
is still a necessary GPU snapshot because scanout buffers are not DPE-owned.
Sampling follows the requested stream rate, independently of the game's render
rate. No per-frame encode/decode or browser canvas hop is added.

The separate DPE Screensharing page includes **KMS monitor capture**, source
selection and live method/handoff diagnostics. Missing helper, blocked permission
changes and unavailable native selection are reported as unavailable choices.
A saved unavailable KMS selection does not silently open the portal. Automatic
capture remains selectable. X11 additionally requires Discord's actual native
system-picker capability; DPE does not force that capability flag.

## Boundaries

This first path supports SDR RGB8 and RGB10 monitors with compatible EGL imports,
linear output allocation and explicit synchronization. Rotated/reflected outputs,
HDR, active overlays, ambiguous clone mappings and incompatible GPU imports are
rejected rather than rendered incorrectly. It does not implement compositor
window extraction; game capture remains useful for isolated application content.
There is no claim that every KMS scene is faster than every portal implementation.

The helper is built and packaged independently of Electron. Capture bundles
include its complete source, standalone build command and GPL-3.0 license. The
existing Electron runtime is reused. NVIDIA and Intel follow capability checks;
this machine can validate only AMD hardware.

The first hardware check identified niri's SDR `ABGR2101010` scanout. Ten-bit
storage does not by itself mean HDR; EGL normalizes it and the GPU snapshot
converts it into the eight-bit output. Premultiplied primary RGB already has the
correct result over the KMS black background when no underlying plane is allowed.
Coverage alpha still requires an opaque format. This follows the kernel's
[plane blending and background rules](https://docs.kernel.org/gpu/drm-kms.html).
The fix does not claim HDR tone mapping or compositor color-management fidelity.

## Native frame delivery

The reviewed native capture thread now wakes when its framebuffer receives a
publication. Frames coalesce without another queue or copy. Native control
messages take priority; update and stop wake the waiting thread, and an idle
heartbeat preserves the prior paused-source behavior. Unsupported scopes retain
the polling fallback. Five narrowly scoped call sites are checked against exact
reviewed hashes and instructions; no Rust object layout is rewritten. See
[the delivery ABI notes](capture-delivery-20261102.md).

## Validation

Final artifacts are under `/mnt/storage/dpe-kms-20261102/`:

- `kms-rgb10-final/native/report.json`: 12-second DP-2 monitor capture, an owned
  fullscreen source requesting 600 FPS, a bounded GPU-pressure phase, native
  1080p60 H.264 hardware encoding and Electron hardware decoding. All delivery,
  dimensions, synchronization and marker checks passed. Producer cleanup was
  clean, with no skipped snapshots.
- `game-h264-final/native/report.json`: the same bounded workload through direct
  game buffer sharing. All checks passed; native diagnostics confirm
  `eventDriven: true`. Both final runs had complete marker coverage and clean
  owned-process shutdown. No synthetic pixels were saved.
- `settings-final/settings.json`: the real Electron Screensharing harness passed
  KMS selection, disabled reasons, startup errors, live GPU-snapshot diagnostics,
  keyboard behavior, themes and narrow/zoomed layout. The rendered settings
  screenshot was inspected.
- `candidate/launch-test.sh`: runnable fresh-profile candidate. The tested KMS
  binary and native adapter are included; Electron is the same previously
  verified binary. Source inventory, hashes and distribution checks pass.

The first KMS run rejected SDR RGB10; the final build fixes that format rather
than weakening the HDR checks. A forced 1080p AV1 game run correctly hit the
existing AMD VCN4 coded-size guard. That single-codec fixture did not negotiate a
fallback, so it is retained as a failed encode test, not counted as a capture
success. H.264 completed the final checks; the guard and codec policy were not
changed in this pass. An initial game-test invocation also rejected an H.264
decoder argument supplied with AV1 before starting native capture.

`bun run lint` and all 646 Python / 348 JavaScript tests passed after the final
code change. The focused protocol and threading fixtures run with ASan/UBSan.
The initial full-suite packaging determinism check overlapped a source edit;
it passed after source freeze. No live Discord account/call or new comparative
benchmark sweep was used. These checks establish working capture and event
delivery on AMD/niri, not measured superiority across systems or NVIDIA/Intel
hardware validation.

## Live startup failure and authorization follow-up

The first real Discord attempts ended before delivering any frame. Temporary
stderr-only diagnostics recorded GSR's `drmfb handle is NULL`: the kernel withheld
GETFB2 handles. The original runs did not record the helper's effective
capabilities, so the precise reason that authorization was ineffective is not
proven. A later retry after restarting Discord recorded `NoNewPrivs=0` and
effective `CAP_SYS_ADMIN`, delivered GPU frames and survived resolution changes.
Only diagnostic logging had changed at that point; this is not evidence that
the logging fixed permissions. No screen contents were saved.

Startup now performs one GET_KMS check after explicit monitor selection and
closes the descriptors before announcing a ready node. A permission failure is
kept distinct from protocol errors, so the chooser can offer **Authorize and
share**. This invokes only fixed `/usr/bin/pkexec --disable-internal-agent
/usr/bin/gsr-kms-server` with the private socket and validated DRM card. No
authorization is requested during discovery or without the explicit action.
The PID and root UID are checked on this path; normal capability launches still
require the user's UID. Cancellation removes the startup socket and cleanup has
a bounded wait even when a root child rejects signals. NoNewPrivs blocks both
file capabilities and pkexec; it is reported without weakening the sandbox.
See the [pkexec manual](https://www.freedesktop.org/software/polkit/docs/master/pkexec.1.html).

The short final host-PipeWire check is under
`/mnt/storage/dpe-kms-stop-20261102/auth-final`: real native H.264 encoding and
hardware decoding passed while changing the producer rate. Mocked native exec
and privilege boundaries cover explicit authorization, denial, wrong peer
credentials, cancellation, and privileged-child socket cleanup with ASan/UBSan.
These tests never invoke an authentication dialog. The actual polkit password
flow remains untested on this installation because its GSR helper already has
working file capabilities.

The final helper also isolates the privileged child's stdin/stdout from the
producer's JSON pipes. The native startup fixture checks that ownership; the
GPU capture check above preceded this final descriptor-only change. The actual
settings harness at `settings-authorize-checked` passed explicit consent,
keyboard activation, waiting/cancellation/retry, themes and responsive layout.
Final lint and all 647 Python / 351 JavaScript tests passed. The existing test
candidate was updated, its temporary stderr wrapper removed, and its source
inventory and package validation checked. Electron was not rebuilt or restarted;
the new controller and authorization UI load on its next restart.
