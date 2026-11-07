# Buffered H.264 decode and complete native compatibility

A fresh-profile synthetic native sender/receiver run reported
`avcodec_receive_frame` = `-11` at `h264_decoder_impl.cpp:598`. The native
`discord::H264DecoderImpl::Decode` treated every negative receive result as
`WEBRTC_VIDEO_CODEC_ERROR`, causing unnecessary decoder recovery/keyframes when
FFmpeg had merely buffered the input. This is a receiver correction. It does not
establish that every remote viewer uses this decoder or remove sender quality
scaling by itself.

[FFmpeg's send/receive contract](https://ffmpeg.org/doxygen/trunk/group__lavc__encdec.html)
says receive-side EAGAIN requests more input and is normal during startup.
[WebRTC's generic decoder](https://webrtc.googlesource.com/src/+/refs/heads/main/modules/video_coding/generic_decoder.cc)
distinguishes `WEBRTC_VIDEO_CODEC_NO_OUTPUT` from negative decode failures.

`scripts/patch_decoder_eagain.py` changes only this case to NO_OUTPUT (`1`).
It applies after Pulse, VA-API HRD and visible capture dimensions. Complete-file
input/output hashes bind both reviewed native versions. Unknown modules fail
preparation; replacement is atomic and preserves hardlinked sources/mode.

The original receive error branch at function-relative `0x182` now targets
`0x1c6`. The existing 96-byte error block at `0x1a9` shares the two identical
logging metadata/file loads to make room for:

```asm
cmp eax, -11
jne original_receive_error
lea r15d, [rax + 12]       ; -11 + 12 = WEBRTC_VIDEO_CODEC_NO_OUTPUT (1)
jmp existing_packet_cleanup
```

The real-error logger's severity check, message, line, error code, ReportError,
`-1` return and packet cleanup remain intact for send and receive failures.
Send-side EAGAIN is deliberately unchanged: it means an input packet was not
accepted and needs separate drain/retry logic. Successful frame conversion,
callbacks and frame release are byte-identical. No function grows and there is
no new allocation or runtime hook.

The new complete-file hashes are also accepted on repeated HRD/dimension
preparation. All six capture/encoder scope generators recognize the exact new
hashes plus the preceding microphone-guard hashes. Their existing individual
ABI/instruction checks still run and their emitted `voice_sha256` remains the
actual final hash. This also fixes a separate integration omission: the earlier
microphone guard could produce successfully written but unsupported capture and
encoder scopes.

## Evidence

- `tests/test_decoder_eagain.py` executes both versions' original and replacement
  instruction bytes with instrumented external targets. It checks send/receive,
  enabled/suppressed logging, EAGAIN and five other negative errors, including
  EOF and invalid data. Every path cleans up exactly once. Only receive-side
  EAGAIN changes from `-1` to `1`; real-error arguments and logging stay intact.
- The combined Pulse/HRD/dimensions/decoder ELF fixture verifies atomic
  preparation, repeat idempotence and unknown-byte rejection.
- Thirty scoped native preparation tests and 36 package/client/CI tests passed.
  `bun run lint` passed. The parent owns the final full suite.
- `out/decoder-eagain/review.json` records exact byte edits and native targets.
  `verification.json` records six real full-chain starting states, repeat
  preparation and both native versions' emitted scopes. Scope verification
  explicitly requires every `supported` field to be true and every scope hash
  to equal the final module hash.
- Synthetic streaming validation is owned by the parent quality investigation;
  branch tests alone do not prove remote playback or eliminate quality drops.

Final native SHA-256 values:

- Current/pinned: `83f278107c08b7717ce9f9c147ee5e161049f1626773d054d514ce98e9223977`
- Previous retained: `f5dae8fc205c8123ceff2f520dfcd50d8f2357de8db221cb5215a92abbcac2e9`

## Staging

Copy `patch_decoder_eagain.py`, `patch_app_audio.py`, `patch_pulse_audio.py`,
`patch_vaapi_hrd.py`, `patch_capture_dimensions.py`, all six changed capture/
encoder scope scripts, and updated `integration/updater/client.py` into the
candidate's normal corresponding locations. The stage/package/fingerprint/CI
lists now include the new script. Regenerate the complete immutable native
generation with `client_native.prepare`; do not replace only a module binary or
reuse scope records with an earlier hash. No Electron rebuild is required.

No running profile, account, call or candidate was accessed or changed by this
subtask.
