# Native capture synchronization, 2026-11-02

The reviewed Discord native capture module passes a five-second **duration** to a
DRM API that requires an **absolute monotonic deadline**. After five seconds of
system uptime, an acquire point that is not yet available immediately returns
`ETIME`. The native caller then abandons DMA-BUF capture and requests MemFd.
The client correction in `integration/video/capture-sync.c` adds the current
monotonic time at that exact call. A delayed-acquire synthetic source reproduced
the failure and passed with the correction, including actual hardware encoding
and decoding. No compositor changes are needed for this client defect.

This is relevant to the synchronization investigation for
[Mutter issue 4563](https://gitlab.gnome.org/GNOME/mutter/-/work_items/4563), but does
not establish that this deadline defect causes the reported GNOME/NVIDIA tearing.
The issue remains open in the fetched record. The tested machine uses AMD; the
fixture uses its own PipeWire daemon and synthetic GPU buffers.

## Existing explicit synchronization and the issue boundary

Mutter 49.2's
[screen-cast source](https://github.com/GNOME/mutter/blob/49.2/src/backends/meta-screen-cast-stream-src.c)
offers DMA-BUF buffers with two additional SyncObj descriptors and mandatory
SyncTimeline metadata when the renderer supports sync-file export and the DRM
device supports timeline sync objects. It publishes the acquire fence before
queueing the buffer and checks the release point before reuse. The same
mechanism remains in the inspected
[current source, commit 432c167](https://github.com/GNOME/mutter/blob/432c167ef2de37a708140b02e29881646fc9c13c/src/backends/meta-stream-source.c).
[OBS's PipeWire consumer, commit 65c320b](https://github.com/obsproject/obs-studio/blob/65c320b46e1af5c7a555161aea6182cdf7a8c524/plugins/linux-pipewire/pipewire.c)
also negotiates and consumes these timelines.

The reviewed Discord **custom Linux capture path already implements that
protocol**. Its buffer offers include the mandatory metadata, and its Vulkan
scaler waits for acquire-point availability, imports the resulting sync-file
semaphore, submits conversion, waits for conversion completion, and signals the
release point. A Wayland source is routed through `LinuxVideoSource`; the retained
client enables `linux-vulkan` in its encoder experiments. A missing picker result
returns an error rather than silently selecting the legacy Chromium capturer.
The retained Chromium/WebRTC PipeWire implementation does not offer SyncTimeline.

Consequently a client can use Mutter's existing synchronized DMA-BUF route when
both sides actually support and negotiate it. Merely setting a Chromium flag,
waiting a fixed number of milliseconds, or declaring an ordinary DMA-BUF
"explicitly synchronized" cannot replace a missing producer fence. Mutter's
[proposed producer-side waiting change !5433](https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/5433)
addresses the case without negotiated timelines. This patch does not implement
that compositor workaround, force MemFd, or invent a fence for that case.

Mutter normally publishes its acquire point before queueing, so the newly fixed
availability timeout may never be exercised there. The delayed-publication test
is a valid negative control for the native consumer, not a reproduction of the
exact GNOME/NVIDIA visual artifact. Actual affected hardware still needs a check
of negotiated sync metadata and the viewer output before claiming issue 4563
resolved in that environment.

## Exact native scope and implementation

The kernel's
[DRM UAPI](https://github.com/torvalds/linux/blob/1f6ee9be92f8df85a8c9a5a78c20fd39c0c21a95/include/uapi/drm/drm.h)
defines `drm_syncobj_timeline_wait.timeout_nsec` as absolute. The matching
[implementation](https://github.com/torvalds/linux/blob/1f6ee9be92f8df85a8c9a5a78c20fd39c0c21a95/drivers/gpu/drm/drm_syncobj.c)
converts it relative to `ktime_get()`. This is the monotonic clock used by the
adapter.

For the pinned `72096efc…` dimensions-corrected module:

| Evidence                                   | Address / value                 |
| ------------------------------------------ | ------------------------------- |
| `VulkanScaler::execute_with_explicit_sync` | `0x118bbb0`, 1733 bytes         |
| Duration loaded into RCX                   | `0x118bc17`, `5000000000`       |
| Corrected direct call                      | `0x118bc29`                     |
| `wait_timeline_point_available`            | `0x11865f0`, 346 bytes          |
| DRM timeline wait implementation           | `0x11907d0`; ioctl `0xc03064ca` |
| Wait mode                                  | `WAIT_AVAILABLE`, flags `4`     |

`scripts/capture_sync_scope.py` checks the complete module hash, full caller and
callee code hashes, exact argument preparation and relative call target. It
supports both reviewed native revisions and their HRD/dimensions variants; other
modules remain untouched. The second revision's corresponding call and callee
are `0x1175ab9` and `0x1170480`. These are derived from ELF symbols, not fixed load
addresses.

The C adapter redirects one call before native engine initialization, with the
reviewed ABI `(i32 drm_fd, i32 sync_fd, u64 point, i64 deadline) -> opaque error
pointer`. It preserves the native return and ownership, restores errno after
reading the clock, saturates overflow to `INT64_MAX`, and leaves unexpected,
zero and negative timeout arguments unchanged. Clock failure preserves the
original native call and increments a diagnostic counter. Executable pages are
restored to RX; the trampoline and native library handle remain loaded for the
process lifetime. The installer is independent of frame pacing.

`captureSyncStats()` reports installation, calls, corrected waits, clock failures
and native wait errors. Separate capture counters report complete SyncTimeline
metadata with the final two valid SyncObj descriptors, malformed metadata, or no
timeline. Observing metadata is not a claim that the fence completed. Absence is
not itself evidence of broken implicit synchronization.

## Validation and retained evidence

The synthetic producer now supports `--sync-timeline MS`, restricted to its
explicit DMA-BUF mode. It exports real DRM timelines, queues each buffer before
new rendering, waits the requested bounded delay, renders its time marker, and
publishes the acquire point only after its GL fence completes. It polls the
consumer's release point before reuse. It has at most one pending GPU operation,
no desktop source, and no saved pixels or packets.

Both six-second checks used 1280×720 at 30 FPS, a 10 ms acquire delay, the same
current native module, AMD VAAPI AV1, and the Electron hardware receiver:

| Observation                         | Original wait           | Corrected wait                |
| ----------------------------------- | ----------------------- | ----------------------------- |
| Valid SyncTimeline frames observed  | 3                       | 179                           |
| Native error / CPU-buffer fallback  | `ETIME`, requests MemFd | None                          |
| Corrected acquire waits / errors    | Adapter absent          | 179 / 0                       |
| Producer acquired / released points | 4 / 0                   | 179 / 171                     |
| Encoded / decoded frames            | 0 / 0                   | 177 / 176                     |
| Unique received markers             | 0                       | 175, all valid, full 1280×720 |
| GPU rendition CPU fallback          | No delivered stream     | 0                             |

The eight outstanding release observations match the eight-buffer pool: the
producer counts a release when reusing a buffer. Receiver marker latency includes
the intentional producer delay and CPU output conversion; it is not GPU execution
time or physical display latency. All owned processes exited after each check.

Reports are retained in
`/mnt/storage/dpe-capture-backends-20261102/{sync-proof,sync-corrected}/report.json`.
The corrected run's private runner compiled the production helper sources and
flags while omitting the then-unfinished, unrelated picker bridge; its weak
registration was absent. The scope, deadline implementation and capture/encoder
adapters were unchanged. Source snapshots and disassembly are under
`out/capture-sync-20261102/`.

Offline ASan/UBSan regressions cover deadline conversion, long uptime, overflow,
clock failure, errno, opaque native errors and actual trampoline dispatch.
Additional tests reject mismatched helper/module metadata, malformed sync-buffer
metadata, invalid producer options, and reports without producer-release or
native-consumption evidence. CI installs libdrm development headers for these
synthetic producer builds. Future integration checks can use
`scripts/check_screenshare.py --sync-timeline --sync-delay-ms 10 --capture-sync`
with the existing private DMA-BUF fixture options.
