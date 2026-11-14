# Capture architecture and ownership

User-facing backend choices are in [screensharing](../screensharing.md) and
[optimized capture](../optimized-screensharing.md). Implementation lives in Rust
workspace crates under `native/`, packaged adapters under `integration/video/`
and `integration/updater/capture-*.cjs`. Read the
[capture skill](../../agents/skills/dpe-capture-checks/SKILL.md) for changes.
Do not copy old C implementation paths from historical experiments.

## Backends

| Path                | Actual contract                                                                                                                                                  |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Native/portal       | Discord's PipeWire source; retain baseline codec/adaptation and application-audio selection.                                                                     |
| Direct Wayland      | Explicit compositor capture protocols, source inventory, buffers/fences and lifecycle; availability depends on protocol support.                                 |
| Bundled game hook   | Per-game Vulkan/OpenGL injection, exported buffer leases and demand-gated capture. No global injection.                                                          |
| KMS monitor         | Privileged primary scanout/cursor export for a monitor, not isolated window capture. Requires explicit grant.                                                    |
| Raw direct GPU      | Eligible bundled game/KMS frames bypass PipeWire video through verified native GPU import and hardware encoding. Audio and other sources retain their own paths. |
| Compatibility relay | Preserves documented OBS/older-hook and unsupported-format/synchronization fallback. It is a distinct measured path.                                             |

Stage coherent bundles with `scripts/stage_capture_backends.py`; helpers, hooks,
protocol XML provenance, shared libraries and manifest hashes must agree. A new
producer plus stale hook or encoder is not a candidate. Helpers affect application
identity, usually reusing Electron. Native module offset/ABI changes require
complete [scope review](native-media.md#native-patch-chain).

## GPU synchronization and ownership

DMA-BUF transport is not automatically zero-copy or free. Track RGB export,
orientation/cursor work, conversion, padding, encoder import and receiver/readback
separately. Validate each plane/modifier/device/stride, descriptor ownership and
acquire/completion fence before reuse. Keep producer leases through actual GPU
consumption. Returning a PipeWire buffer does not prove completion; never weaken
fences or overwrite an outstanding surface to inflate throughput.

Direct/relay mode negotiates explicit sync/export support and retains a safe
copy fallback. The GL visibility bridge must actually activate, not merely be
requested. Forced-copy allocation may retain the old path even with the flag;
copy-versus-relay runs can compare complete paths rather than one isolated copy.
The GL hook folds orientation into its existing blit. Source allocation mode can
remain fixed when a consumer changes handoff mode.

Rust owners bound frame pools, fd/socket lifetimes, Wayland resources, VAAPI/VPP
surfaces and native callbacks. Keep publication wakeup/reset and terminal
notifications ordered: error 2011 can come from source/consumer startup, not
just codec negotiation. A stale preview/catalog response must not resurrect a
cancelled owner. Keep retry counts bounded and explicit, not a capture loop that
retries negotiation indefinitely. Completion and teardown release each resource
once, including helper exit, peer EOF and cancelled startup.

Native13's Vulkan repair handles SDR FP16/normalized swapchains by validating
source optimal-tiling and actual destination modifier blit features, then using
the existing eight-bit export image. HDR/unknown/integer formats stay unsupported.
No extra export buffer/CPU conversion/readback is introduced. GLX success alone
did not qualify Vulkan. See [Native12/13 evidence](benchmarks.md#native12-resource-comparison).

## Source lifecycle and admission

Admission happens before expensive capture/export. With no consumer, demand-gated
capture emits at most bootstrap work, then pauses; previews have their own bounded
cadence. Do not count allocations or polled frames as new image content. A reused
DMA-BUF may contain changed pixels; static skipping needs trustworthy damage and
cursor information. No unqualified damage-aware or asynchronous-depth optimization
belongs in the default path.

Keep request IDs across main→renderer→transport source-ended callbacks. Lost
helper/Wayland/KMS sources clear exactly the active native selection once;
stale/duplicate ended events cannot kill a replacement. Game surface STOP allows
five seconds for recreation before ending a permanent loss. Native ownership
still frees active descriptors, not an unsolicited renderer callback.

Exercise resize/fullscreen/context recreation, 60→30→60 rate changes,
pause/resume, source loss/replacement, cancellation and shutdown. Single leased
game textures intentionally limit admission under severe pressure; adding slots
trades memory/latency against throughput and needs measurement. Never block game
present waiting for a viewer or accumulate old frames. Preserve source FPS,
encoder congestion control and honest native rate-limiter drop diagnostics.

## Picker handoff and previews

Native/Vanilla choices retain portal behavior. An Optimized commit marks a strict
owner-checked one-shot handoff before invoking Discord's original start callback.
React cleanup must preserve that committed selection; ordinary dismissal disarms
it. Retain source/owner binding, 15-second expiry and one-time consumption. Do not
suspend the game broker while a committed asynchronous native request is pending.

Confirmed optimized monitors can skip the audio picker using their active request
identity without a portal node; games/manual choices retain their own selection.
Source loss retires associated audio routes. Monitor numbers are spatially sorted
only when connector→logical-position matches are complete and unambiguous. Use
standard xdg-output coordinates, then stable connector ties; missing metadata
preserves original order. Tokens retain connector identity independently of numbers.

Persistent previews must not keep hidden/unselected full capture work active.
Bound thumbnail readbacks separately from full-resolution hardware paths, clear
pending UI state on teardown, and use the shared loading/error icon treatment.
A preview advancing does not prove a stream or establish encoder quality.

## Permission status and initial setup

`configured` means a trusted installation marker exists; only `allowed` authorizes
capture. `needsUpdate`/`canRevoke` describe maintenance paths. Status reads never
prompt, install, migrate or revoke. Cancelled/failed required revocation must not
save experimental capture off as if removal succeeded. Never substitute a status
boolean for the executable grant or add broad capabilities to Discord.

Initial authenticated setup installs fixed root-owned KMS, maintenance code,
public key, per-user enablement and fixed polkit entrypoints. It does not authorize
an arbitrary installer/interpreter/user-selected root executable. Old setup needs
explicit migration once. Revocation disables maintenance before removing rules;
re-enabling requires setup approval.

## Verified KMS maintenance

The exact installed initial tuple can refresh its executable grant offline.
Changed code needs a signed release `capture_maintenance` record and independently
fetched matching approval. Root validates GPG, channel/track/source/manifest,
component hashes and per-user/global rollback counters before writing fixed
destinations atomically. It never imports candidate-writable code, follows leaf
symlinks or trusts user-editable updater records as root authority. Failed
verification cannot fall back to an authentication installer.

The mode-0744 KMS server runs through the fixed pkexec action. Before DRM export,
it checks the live Discord gate's kernel SO_PEERCRED, exact granted executable
inode/change metadata, random 256-bit capability and exact worker. A live
kernel-authenticated gate also attests the selected application path; Electron
clears procfs environment, so it cannot provide that identity. Root rechecks live
inode metadata before publication. A different process or older application
cannot nominate another app's approved code. There is one active executable grant
per user, not a growing allowlist.

Keep the gate open for the worker lifetime. Gate EOF, grant revocation and
Discord exit win over simultaneously ready frame requests. Changing the grant
retires prior exporters. No per-frame hashing/network call is added. This checks
executable/code selection; it does not isolate software already controlling the
approved Electron process. Modified/unsigned development payloads need explicit
administrator approval.

Restricted Electron children may inherit NoNewPrivs=1 and be unable to use pkexec.
Probe an unprivileged systemd user service; use it only when it actually reports
NoNewPrivs=0. Forward literal arguments and selected session variables through
pipes, never preload variables. Each worker owns a unique service and stops on
EOF/unit stop. Missing tools/restricted managers preserve a blocked result.

Native16 validation rejected unrelated Node and old DPE callers, moved grants
without new authentication, produced four changing thumbnail hashes and delivered
613 receiver frames in 10.25 seconds with no full-resolution CPU readbacks or
capture failures. Evidence: `out/live-capture-private/{kms-maintenance-*,native16-*}`.
Those were explicitly authorized historical checks, not permission for account
calls or privileged installation now. Native15's procfs-environment assumption
failed despite passing launch checks; do not restore it.

## Qualification and remaining work

Use real native sender/receiver checks and [matched benchmarks](benchmarks.md).
Hold actual dimensions, codec/bitrate/receiver/delivery/pressure equal, include
source/helper/compositor cost and retain failures. Bundled game/KMS PipeWire bypass
does not mean application audio, direct compositor protocols or stock OBS bypass
it. Intel/NVIDIA, HDR, older GPUs/CPUs, long sessions, multi-GPU modifiers,
equal-quality WAN viewers and device-wide power need their own evidence.
Do not add a preset or claim a whole-system saving from helper-only counters.

## Cursor and diagnostics boundaries

X11/XWayland game capture carries the exact protocol window ID and composes a
bounded XFixes cursor on the owned GPU output. Query through a private asynchronous
XCB connection with at most one outstanding batch and a 100 ms deadline; cap
sprites at 256×256, upload on changed serial, and suppress ambiguous scale or
out-of-window state. No game-image CPU readback is required. Native Wayland
texture exports do not provide a global OS cursor: use an available compositor
cursor session for the exact output/toplevel, with transformed source coordinates,
ready-event hotspot updates and immediate leave suppression. Missing protocol
support stays a real limitation; do not draw a fake theme arrow or acquire an
extra portal stream silently. Game-rendered cursors remain part of the source.

The Screensharing diagnostics adapter whitelists observations rather than
exposing full native stats/source catalogs. Scope IPC to trusted main frames and
random request IDs, coalesce requests and bound replies. Poll only a visible tab.
Saved preference, requested limit, negotiated limit and actual throughput are
separate facts. Missing values display unavailable/not reported; hardware labels
need runtime evidence. Process-wide AV1 rejection counts cannot always be
attributed to the current share because camera encoders can also increment them.

## Package removal

The bootstrap uninstaller serializes with setup/maintenance/revoke and removes
grants before policy/helper files so active exporters observe revocation. Package
hooks run on real removal only: Debian upgrades/deconfigure, RPM remaining-instance
counts and Arch upgrade paths must not remove authorization. Preserve unrelated
profiles, settings and downloads. Other root-owned managed DPE installs retain
shared helpers; remove only grants attributable to the removed channel/track until
the last install. Legacy unattributed records wait for final removal. Portable
installs need explicit cleanup because arbitrary paths are not discoverable.

Final removal clears only known UID grants, maintenance records/rules/actions,
trusted helper/verifier/library/key/authority files, known bytecode and interrupted
atomic writes. Unknown files stay. Check trusted ancestor ownership and reject
traversal/writable directories; removing a leaf symlink removes only that link.
Debian AppArmor cleanup unloads/removes only DPE's named profile, including legacy
conffile layout. Repeated cleanup is harmless. Real nFPM-generated package hook
fixtures are in `out/package-uninstall-check/`; stand-in dispatch checks do not
prove every full distro removal transaction.
