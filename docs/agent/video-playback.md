# OpenAsar playback crash and flag audit

## Reproduction on 27 October 2026

The pinned OpenAsar Performance preset enables `EnableDrDc` by default, including
when no Focus preference is saved. The existing adapter merged this into the
launcher's hardware-decoding features. Chromium's `gpu/config/gpu_finch_features.cc`
marks DrDc unsupported outside its supported platform branches with
`NOT SUPPORTED. DO NOT ENABLE!`; Linux is in that unsupported branch.

The real pinned command-line module reproduced the failure on the retained
Electron 46.0.0-alpha.1 runtime. On native Wayland, starting the generated H.264
clip crashed the GPU process with exit code 133 and disconnected the media
pipeline. On Xwayland, the preset failed to create a WebGL context. Adding only
`EnableDrDc` to the otherwise passing launcher defaults also reproduced failure.

The console's Vulkan incompatibility message was not sufficient to diagnose the
problem: OpenAsar already disabled the `Vulkan` feature. Removing DrDc fixed the
test while leaving that setting unchanged.

Source: [pinned OpenAsar command-line module](https://github.com/GooseMod/OpenAsar/blob/5a44615cad9db23ae57573c498b26bae4e6e255b/src/cmdSwitches.js).
Chromium was inspected in the retained compiler checkout at commit
`b12138756573818940df44df8f8aba7b4238f7d8`, including `kEnableDrDc` and
`ShouldEnableDrDc()` in `gpu/config/gpu_finch_features.cc`.

## Argument isolation and fix

The tests used Arch Linux, niri, Mesa 26.2.4 and a Radeon RX 7900 GRE. Every run
used a fresh profile and synthetic media. The retained compiler archive matched
the current update policy's SHA-256; the runtime executable matched the archive.
The locked OpenAsar archive was verified and its command-line source matched the
installed package. No compiler was started and no installed files were changed.

- Launcher defaults alone passed hardware WebGL and VA-API H.264 playback.
- Removing GPU rasterization, zero-copy, the blocklist bypass, overlays, GPU
  preference, OpenH264 enablement, either decoding feature, canvas rasterization
  or the Skia feature individually from the full preset did not fix the failure.
- Removing only `EnableDrDc` passed, with and without explicitly disabling it.
  The complete remaining preset passed on Wayland and Xwayland and on a repeat.
- All 21 other preset switches and feature toggles passed individually when
  added to the launcher defaults. This is evidence for this driver, not a claim
  that forcing those settings works on every GPU.

`integration/updater/flags.cjs` now filters incoming `EnableDrDc` entries,
including parameterized and field-trial forms. It also omits OpenAsar's automatic
`--ignore-gpu-blocklist`, restoring Chromium's driver protections. Other Focus
settings and hardware decoding remain enabled. Explicit flags supplied through
the launcher are preserved, so an intentional user override can still reintroduce
unsupported settings. Pinned upstream sources and patches are unchanged.

Unit tests cover feature filtering, empty feature lists, explicit overrides,
unrelated preset settings and routing through the bootstrap transform. The native
probe now runs the actual pinned module through the adapter before app readiness,
records effective switches, and retains GPU exits and media errors on failure.
Earlier graphics checks exercised the launcher flags without OpenAsar, which is
why they missed this combination.

## Validation and reproduction

All six combinations of Performance, Battery Life and Balanced with Wayland or
Xwayland passed after the fix. Each used `VaapiVideoDecoder`, hardware WebGL and
software WebCodecs encoding for H.264, VP8, VP9 and AV1, with no GPU exits.
The final before/after comparison used the same pinned module and current fixture,
changing only the compatibility adapter. Before: Wayland GPU crash / Xwayland
WebGL failure. After: both passed.

```sh
uv run python scripts/check_graphics.py /path/to/verified-runtime/electron \
  --output out/fresh-performance-wayland \
  --openasar-source /path/to/package/bootstrap/src/cmdSwitches.js \
  --preset perf \
  --flag=--ozone-platform=wayland \
  --flag=--enable-features=AcceleratedVideoDecoder,AcceleratedVideoDecodeLinuxGL
```

Repeat with `--preset battery` / `balanced` and `--flag=--ozone-platform=x11`,
always choosing a fresh output. Use the source matching `config/openasar.lock.json`.
Adding `EnableDrDc` explicitly to the launcher's enabled feature list reproduces
the unsupported path even with the adapter, since explicit overrides are retained.

`bun run test` passed 238 Python and 61 JavaScript tests. `bun run lint` passed.
The [saved evidence](../evidence/video-playback-flags.json) contains the flag
matrix, runtime/source hashes, before/after results and effective switches.
Raw local reports are in `out/video-playback-investigation/` in this worktree.

## Limits

This reproduces a GPU crash consistent with the report; no failing user media or
crash dump was supplied to establish that it was the same incident. No account,
live Discord video or call was tested. NVIDIA, Intel and native X11 sessions
remain untested. The fixture does not capture a camera, microphone or desktop.

Forced software-only H.264 playback separately failed with
`DECODER_ERROR_NOT_SUPPORTED` / `kUnsupportedConfig`, including without OpenAsar
and with the system OpenH264 library path supplied. Those runs had no GPU crash;
they do not establish a working software fallback and are retained in the evidence.
The fix keeps the passing hardware decode path rather than disabling acceleration.
