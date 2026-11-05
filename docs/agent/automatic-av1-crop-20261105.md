# Automatic AV1 source cropping, 5 November 2026

Follow-up: [native two-row padding](native-av1-two-row-padding-20261105.md) now
uses 1082 instead of 1088 where supported, with the same automatic crop metadata.
The measurements below describe the earlier centred-padding implementation.
The [software decoder follow-up](software-av1-crop-20261105.md) also adds
automatic cropping to DPE's native dav1d path.

This extends the [native-size AMD padding work](receiver-clock-av1-padding-20261104.md).
The sender still preserves original pixels within the aligned coded surface;
the DPE Electron decoder now exposes the original source rectangle automatically.
No setting or resolution heuristic is added. Genuine 1920×1088 content remains
1920×1088.

## Implementation

The private VAAPI AV1 encoder adds the source rectangle to padded keyframes
using the syntax in [AOM cropping proposal #346](https://github.com/AOMediaCodec/av1-spec/pull/346).
This is an **open proposal**, not a ratified AV1 feature. The metadata OBU uses
type 32, carries source width/height and chroma-aligned offsets, and precedes the
frame header. Its 12 bytes are reannounced at every keyframe. There is no new
Discord packet or control channel. Aligned AV1, HEVC and the native encoder
paths are unchanged.

`integration/video/av1-crop.h` contains a bounded C/C++ writer/parser, shared
verbatim with the owned Electron follow-up patch. The parser tracks sequence,
keyframe and crop lifetime, ignores other temporal/spatial layers and fails
closed on malformed input. Missing crop information never triggers a guess.

The Electron AV1 decoder snapshots crop state by input timestamp. When an
asynchronously decoded frame arrives, it validates that snapshot against the
decoded frame's visible and coded bounds, then wraps the same frame storage
with the requested visible rectangle. Decoder references retain their full
geometry. This introduces no texture copy, GPU pass, pixel inspection, readback
or resampling. The existing Discord frame wrapper and presentation path
preserve the resulting visible bounds. The snapshot queue is bounded.

The implementation applies to Electron AV1 decoding regardless of GPU vendor;
only the existing affected AMD sender path currently emits padding metadata.
It does not add AMD restrictions to Intel/NVIDIA. Discord's separate native
dav1d software fallback bypasses this Electron interface and therefore keeps
the padded picture. Old DPE senders also lack the metadata. Hardware not
available locally was not runtime-tested.

## Validation

Artifacts: `out/automatic-av1-crop-20261105/`. All sources were generated;
no account, call, desktop or physical camera was used. These are native Discord
loopback checks, not evidence of a live Discord server forwarding session.

The complete path includes generated 240 FPS PipeWire DMA-BUF input, 60 FPS
capture, private VAAPI AV1 encoding, Discord's native RTP packetization and
depacketization, Electron decode and video-element presentation. All checks
passed, including expected dimensions and all four picture edges:

| Receiver                              | Source            | Displayed | Checked edge frames |
| ------------------------------------- | ----------------- | --------- | ------------------- |
| Previous DPE Electron, no crop parser | 1920×1080         | 1920×1088 | 353                 |
| Updated Electron                      | 1920×1080         | 1920×1080 | 355                 |
| Updated Electron                      | 1918×1078         | 1918×1078 | 353                 |
| Updated Electron                      | Genuine 1920×1088 | 1920×1088 | 354                 |

A separate native dav1d software-decoder loopback also passed with the expected
padded 1920×1088 output. The final staged Electron binary passed a repeat 1080p
check, with 349 checked edge frames; its independently built helper was
byte-identical to the staged helper.

The previous receiver also passed 353 padding checks. This confirms its native
transport and decoder accept the metadata without requiring the new parser;
it does not establish compatibility with every released Discord client.

The generated VAAPI pixel test passed all ten HEVC/AV1 combinations at 256×256,
640×360, 1280×720, 1920×1080 and 1918×1078. It compares decoded luma stripes,
source edges and neutral padding chroma at their original coordinates.
Lifecycle tests passed suspension, resize, recovery after rejected updates and
fresh-decoder keyframe recovery. They also verify crop persistence and the
rectangle on resized padded AV1 output. Parser checks cover bounds, truncation,
keyframe resets, malformed trailing bits and 10,000 randomized inputs under
sanitizers, plus C++20 compilation.

The initial repository validation passed: `bun run lint`, 704 Python tests and
424 JavaScript tests (three environment-dependent skips). During a later rerun,
concurrent desktop media-controls changes broke five unrelated startup test
mocks; the 704 Python tests and focused crop/HEVC/frame checks still passed.
After the concurrent mock updates arrived, the final JavaScript rerun passed
425 tests, with the same three skips. Final lint also passed.
The follow-up patches reproduce
locked Electron tree `173497ada45421d3e06890f5109551ebee4b0674` using a temporary
index; the retained build index was preserved. The retained runtime was rebuilt
incrementally and staged with the current helper at
`out/receiver-investigation-20261104/candidate/`. Existing sender/viewer launchers
use it after both processes restart. No account state or running process was
changed during staging. Binary hashes are recorded in `staged.json` in the
artifact directory.

Registering the shared header in Electron's `filenames.gni` regenerated the
build files. The first resumed build lacked `gn` on `PATH`; adding the retained
`buildtools/linux64` directory allowed the incremental build to complete.
An additional optional `gn check //electron:electron_lib` still reports private
Blink include/dependency visibility errors in the existing `module.cc` and
`discord_video_source.cc`; it is not recorded as a passing check. These do not
prevent the successful compile/link or native fixture runs above.

### FFmpeg raw-OBU demuxer limitation

The installed FFmpeg raw AV1 demuxer's `av1_frame_merge` coded-bitstream parser
rejects this proposed metadata's non-byte-aligned trailing bit. The same
unaltered packets decode successfully through libdav1d in IVF and through the
actual Discord native transport/Electron path. Generated fixture files now use
IVF to preserve encoder packet boundaries, without rewriting or stripping any
encoded bytes. Lifecycle validation permits only the specific older-dav1d
unknown-metadata diagnostic, not other decode errors. The original raw-OBU
failure is retained in `padding-compat/`.

Do not describe this as universal AV1 tooling compatibility. Revisit the syntax
and assigned metadata type if AOM changes or finalizes the proposal. No decoder
crop support is assumed merely because a client can decode AV1.
