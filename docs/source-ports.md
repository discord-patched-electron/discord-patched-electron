# Candidate source ports

These are **source-only candidates**, not verified working Electron releases.
They preserve Discord's public native bridge. Dependency patch application,
compilation, private module ABI behavior and stream/audio tests remain gates.

## Structure

`patches/discord/44/integration.patch` remains the exact imported Discord delta.
Each active major has a separate `patches/ports/<major>/compatibility.patch`.
`config/sources.lock.json` pins the upstream anchor, compatibility patch checksum,
replacement paths and expected assembled Git tree.

Assembly applies the imported delta except for an explicit, literal list of files
whose changes have been reviewed, then applies the compatibility patch. Every
excluded path must either occur in that patch or have an `upstream_equivalents`
explanation in the lock. Missing explanations, wildcards and altered checksums
fail before application. No exclusions are discovered automatically from errors.
On the exact anchor commit, the final tree must match the recorded candidate.
Later patch/minor updates may reuse the same series with three-way application;
an incompatible change stops the build rather than selecting a conflict side.

## Changes made for Electron 45

- Preserve the newer upstream WER and static-ANGLE packaging behavior; these
  Linux builds do not use Discord's older Windows WER implementation.
- Do not resurrect the removed sandboxed-renderer module list. Upstream now uses
  the shared renderer list, which contains the imported Discord linked binding.
- Move `deviceScaleFactor` into the native WebContents property table, matching
  upstream's move away from the old JavaScript accessor initialization block.
- Register Discord's overlay, frame-rate, device-scale and capture methods using
  upstream's current object template conventions without restoring removed APIs.
- Retain the newer `ElectronFrame` interface and its existing methods alongside
  Discord's frame-capture/target-lease methods.
- Keep upstream's MaybeLocal conversion failure propagation instead of imposing
  Discord 44's global `ToLocalChecked` workaround. Any incompatible new call site
  must be adapted locally and proven by compilation.
- The obsolete Chromium `cherry-pick-69403d85b78b.patch` received only an email
  subject formatting change in the imported delta. That metadata edit is omitted.

## Additional changes for Electron 46 and 47

- Retain cppgc ownership/tracing for BrowserWindow and pass the current raw view
  pointer to `SetContentView`; do not restore obsolete V8 handles/weak pointers.
- Add only Discord's new window-button appearance wrapper, keeping upstream's
  direct native-window forwarding for the existing methods.
- Preserve the newer paint event object and device scale while adding the
  overlay frame-send condition.
- Keep the `CopyFromSurfaceImpl` API name with the added selection-bounds hook.
- Preserve Mojo's `handle<platform>` heap snapshot type alongside the added
  capture interfaces, and avoid reintroducing removed message-queue declarations.
- Keep the current macOS framework bundle dependency layout. Linux is the
  supported build target; this port does not claim macOS/Windows validation.

## Evidence and limitations

Fresh worktrees for all three pinned targets reconstructed their exact candidate
trees locally. The ordinary GitHub check workflow now repeats that reconstruction
and the original imported-fork reconstruction on standard hosted runners.
Source reports explicitly retain `dependencies_patched: false`, `compiled: false`
and `runtime_validated: false`.

Discord's Chromium, FFmpeg and other dependency patches remain imported and
ordered. Their applicability to newer Chromium versions is not established by
Electron source assembly. Runtime publication must wait for those patches to
apply, C++ compilation to succeed and the functional validation matrix to pass.

## Electron 46 dependency refresh

`patches/ports/46/dependency-context.patch` is a checksum-pinned additive layer
applied after the original import and Electron API port. It refreshes dependency
patches against Chromium 158 / its current FFmpeg. Fresh assembly checks the final
Git tree. Cached source checkouts must match that tree with no unrecorded edits.

Most changes refresh surrounding context. The substantive adaptations preserve
upstream OpenH264 NULL cleanup, failed-reinitialization protection and its flush
callback; retain Discord's packet-to-frame metadata matching; resolve all pending
restriction-target promises; and preserve frame-scoped audio authorization.
Upstream already carries the WebNN lock annotation, so that redundant patch is
explicitly removed from the dependency series. Upstream's macOS locale resolver
is retained alongside Discord's empty-localization guard. Exact reasons are
recorded in `ports.46.dependency_notes`.

The stale generated FFmpeg roll is replaced by `roll_ffmpeg_linux_x64.patch`.
It was generated using the patched `media/ffmpeg/scripts/build_ffmpeg.py linux
x64 --branding Chrome`, followed by `robosushi.py --import-only`, with Chromium's
pinned Clang. The standalone FFmpeg build and upstream license checks pass. The
new Linux x64 source lists override the upstream GN lists only for that target;
other target lists are retained. Only Linux x64 is qualified by this project.
The generated configuration preserves Discord's GIF, AAC/MP4/file and dynamically
loaded OpenH264 functionality. It is committed as a patch, so ordinary CI builds
do not regenerate it or need the standalone generation dependencies.

## Electron 46 frame-capture lifetime

`patches/ports/46/frame-capture-gc.patch` moves Discord's `FrameCapture` onto
upstream's `gin::Wrappable` / cppgc allocation, tracing and weak-cell callbacks.
Electron 46 removed the old `Pinnable` and deprecated wrapper APIs. An active
capture keeps itself alive until its asynchronous `stopped` event is delivered;
closing its Mojo pipes and capture sources precedes releasing that root. Wrapper
allocation failure also follows this cleanup path. The dedicated wrapper pointer
tag is appended without changing existing tags.

Both affected C++ translation units compile with the pinned prerelease toolchain,
and fresh assembly reproduces the locked tree. Full runtime lifecycle validation
remains required before publication.
