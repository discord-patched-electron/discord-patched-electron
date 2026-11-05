# Native software AV1 cropping, 5 November 2026

Extends [AV1 crop metadata](automatic-av1-crop-20261105.md) and
[two-row AMD padding](native-av1-two-row-padding-20261105.md) to DPE's native
WebRTC dav1d decoder. No Electron rebuild, encoder change or additional setting
is needed. An updated sender must include the source rectangle; missing
metadata never triggers a resolution guess. Unmodified viewers still retain
padding.

## Implementation and ABI scope

`integration/video/av1-software-crop.c` intercepts six reviewed call/jump sites
in Discord's dav1d wrapper: open, send, receive, Release and both destructor
variants. `encoder_scope.py` checks the whole native module identity, the
complete five wrapper-function hashes, named C entry points and exact branch
targets. Both supported native module families are reviewed. The installed
adapter checks executable segments and original branches, retains native
targets, restores executable page protections and runs before engine
initialization. Unsupported native layouts retain the existing compatibility
guard failure behaviour.

The adapter uses only reviewed prefixes of the
[public dav1d data/picture ABI](https://github.com/videolan/dav1d/tree/master/include/dav1d).
It does not link the host's libdav1d. The input and output metadata timestamp
offsets are 24 and 72; picture width/height begin at 56. The native consumer's
plane, dimension and stride loads match the public prefixes. Both native
wrappers construct RTP/NTP timing from EncodedImage separately, and request a
maximum frame delay of one.

The shared bounded AV1 parser tracks metadata independently for each decoder
context. Each accepted input gets a private monotonically increasing dav1d
metadata timestamp and a snapshot of its crop state. On output, a constant-time
bounded lookup restores the original timestamp and selects that frame's crop.
This also prevents a newly submitted keyframe or resolution change from
affecting a delayed picture. Rejected/EAGAIN inputs do not commit crop state;
retry timestamps are restored. Registry lookup briefly takes a mutex; native
decode never holds a lock shared by different streams.

For a valid 8-bit I420 or I444 picture, the adapter adjusts the caller's plane
pointers and width/height before the existing WebRTC plane wrapper runs. It
keeps strides, frame/sequence headers, reference handles and allocator data.
The decoder's reference pictures and allocation geometry stay intact. Bounds,
chroma alignment, strides and pointer arithmetic are checked before any view
field changes. There is no pixel copy, resampling, readback or GPU pass.
Discord's reviewed native decoder already rejects non-8-bit output; this does
not introduce new high-bit-depth decoder support.

[dav1d picture ownership](https://github.com/videolan/dav1d/blob/master/src/picture.c)
retains the original allocation in its reference/allocator context. Picture
unref releases that reference, not the adjusted visible plane pointer. Closing
or recreating a decoder removes its crop state, including on pointer reuse.
Both registry size and snapshots per context are capped at 128. Missing or
evicted snapshots leave geometry unchanged. Stats report submissions, applied
and rejected crops, unmatched outputs and active contexts under
`encoderGuard.softwareAv1Crop`; software decode remains labelled as software.

## Validation

Artifacts: `out/software-av1-crop-20261105/`. All media was generated. Tests
use private Discord native transport loopback, not an account call or proof of
Discord server forwarding. Cropping uses the same open AOM proposal as the
hardware path, with its [existing compatibility limitations](automatic-av1-crop-20261105.md#ffmpeg-raw-obu-demuxer-limitation).

The first full software-native loopback decoded 357 checked 1080p frames from
1082-line AV1 with exact 1920×1080 output and all picture edges preserved.
Software crop stats reported 357 submitted/applied crops, zero rejects or
unmatched outputs, and zero remaining contexts after teardown.

The C fixture verifies original plane storage and strides, delayed/reordered
output after a new keyframe, metadata-free key reset, EAGAIN retry, independent
contexts, context pointer reuse, bounded eviction, invalid crop geometry and
registry teardown. It passed ASan/UBSan. Native loopbacks also exercise real
dav1d picture release. JavaScript tests cover the scoped
adapter integration and diagnostics; C/C++ parser tests remain in place.

Final native results, including the bounded timestamp lookup:

| Native module / decoder | Source            | Displayed | Edge checks | Applied crops      |
| ----------------------- | ----------------- | --------- | ----------- | ------------------ |
| Retained / dav1d        | 1920×1080         | 1920×1080 | 353         | 353                |
| Retained / dav1d        | 1918×1078         | 1918×1078 | 177         | 177                |
| Retained / dav1d        | Genuine 1920×1088 | 1920×1088 | 355         | 0                  |
| Retained / Electron     | 1920×1080         | 1920×1080 | 353         | 0 in software hook |
| Pinned / dav1d          | 1920×1080         | 1920×1080 | 357         | 357                |

All passed, with zero software-crop rejects, unmatched outputs or live contexts
after teardown. The irregular test used 30 FPS with 120 FPS source updates;
the other retained-module tests used 60 FPS with 240 FPS source updates.
The separately prepared pinned module also passed the full software 1080p
loopback. Its first fixture attempt lacked `libmediapipe.so` beside the isolated
module and failed before decoding; supplying the matching dependency fixed the
test setup. The dependency hash matches the pinned module's retained package.

Repository checks passed: `bun run lint`, 708 Python tests and 427 JavaScript
tests, with three environment-dependent JavaScript skips. No physical camera,
account, message or call was opened.

The tested helper, adapter and regenerated encoder scope were staged in the
existing experimental candidate. All five final native fixture helpers are
byte-identical to the staged helper. The Electron runtime, native-module file
and private encoder backend were preserved; hashes are in `staged.json`.
Running instances were left alone and need a restart to load the update.
