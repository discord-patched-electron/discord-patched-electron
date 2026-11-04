# Linux HEVC hardware encoding: device support versus Discord backend

Historical native-module audit. The working tree now supplies the missing
backend through a [separate GPU VA-API adapter](hevc-av1-chroma-20261104.md).

The AMD render device supports HEVC Main and Main10 encoding. A bounded synthetic
FFmpeg check on `/dev/dri/renderD128` encoded 30 frames at 640×360 with
`hevc_vaapi`, then decoded all 30 without error. This is actual hardware encoding,
not merely an advertised VA profile. Evidence is in `out/hevc-native-audit/`:
`vainfo.log`, `encode.log`, `decode.log`, and the synthetic `hardware.hevc` file.

Discord's retained Linux voice module is a different encoder implementation.
Its reviewed C ABI defines H264=0, H265=1, AV1=2 and its generic encoder factory
has HEVC branches. However, the compiled VAAPI Rust backend specializes only
AV1 and H264 encoders (`cros_codecs::encoder::av1` and `::h264`). There is no
HEVC VAAPI encoder specialization or FFmpeg encoding wrapper in that module.
FFmpeg-linked decode functionality and HEVC RTP packetization/parsing do not
supply an encoder. The native capability result `H265 encode:false` therefore
matches the shipped backend on this AMD device; it is not evidence of missing
HEVC hardware.

Inspection artifacts retain the demangled symbol table, exact native factory
disassembly, and DWARF `SessionOptions`/`EncoderType` definitions. Native locations
for this retained module include `DiscordEncoderFactory::Create` at `0x443ba0`,
its supported-format routine at `0x443880`, and the AMD VAAPI encoder constructor
at `0xe55b30`. Addresses are evidence only, not portable patch offsets.

A flag-only enable would advertise an encoder the backend cannot instantiate.
A real addition could use the already-reviewed `cc_encoder_*` C ABI, but would
need a new backend covering create/update/destroy, native GPU-frame import and
synchronization, encoded-frame callbacks, and native capability probing. There
is no existing FFmpeg encoder object in the module that can simply be selected.
Any such backend must pass Discord sender/receiver HEVC roundtrip checks before
its capability is exposed. The standalone FFmpeg check does not establish that
Discord integration.

No production capability was forced, native module modified, game/account used,
or actual screenshare captured by this audit. No HEVC enablement is claimed.
