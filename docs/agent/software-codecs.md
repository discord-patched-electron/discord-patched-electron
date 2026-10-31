# Software codec initialization and sandbox fix

Investigated on 2026-10-27 after the automated round-trip checks exposed a
software H.264 decoder failure. [Evidence](../evidence/software-codecs.json).
This supersedes the unresolved software H.264 limitation in the earlier
[playback](video-playback.md) and [flags-file](flags-file.md) reports.

## Cause

The verified Electron 46 / Chromium 158 build has two different OpenH264 paths:

- Chromium's software `OpenH264VideoEncoder` uses its built-in implementation.
  It produced twelve valid H.264 chunks even when external decoder loading
  failed. VP8/VP9/AV1 software encoders also worked with hardware encoding off.
- Discord's imported FFmpeg patch supplies the H.264 decoder through the
  external `libopenh264` wrapper. `FFmpegVideoDecoder::ConfigureDecoder` reads
  `--enable-libopenh264` and `--openh264-library-path`, then `avcodec_open2`
  invokes `ff_libopenh264_load_library`, which calls `dlopen` during decoder
  initialization. This happens after the renderer sandbox starts.

Generated configuration confirms `CONFIG_H264_DECODER=0` and
`CONFIG_LIBOPENH264_DECODER=1`: there is no ordinary FFmpeg H.264 decoder to
fall back to. The dynamic decoder is registered, but its late library load
fails under the renderer sandbox. Capability queries still advertise H.264
support and cannot establish that this library can actually be initialized.

Evidence isolating that boundary:

1. Calling `avcodec_find_decoder_by_name("libopenh264")` and `avcodec_open2`
   directly against the verified `libffmpeg.so`, with the cached library path,
   succeeded. The decoder was present and its library was usable.
2. With both video acceleration paths disabled, VP8/VP9/AV1 software round trips
   passed; H.264 produced encoded bytes but failed decoder initialization.
   Media-domain messages confirmed the selected library path and wrapper.
3. An isolated `--no-sandbox` diagnostic made H.264 decoding work. This switch
   is not part of the fix.
4. Mapping the same library before starting Electron made H.264 work with
   the sandbox enabled, including MP4 playback and WebCodecs round trips.

The retained source inspected was
`prerelease/f6a6a79b20ad975b5e64ad64d45df92dd007d514785f46cd66cd58baed91a390/checkout/src`:
`media/filters/ffmpeg_video_decoder.cc`, `media/video/openh264_video_encoder.cc`,
and `third_party/ffmpeg/libavcodec/libopenh264{,dec}.c`. The compiled flags and
generated FFmpeg decoder/filter lists were checked against the retained build.
No compiler objects, pinned patches, or runtime binaries were changed.

## Launcher fix

`integration/launcher/codec-preload.sh` reads the final resolved flags. Only when
OpenH264 is requested and its library exists does it add that library to
`LD_PRELOAD` before the client exec. The renderer inherits the mapped library,
so its later `dlopen` can reuse it without opening a file outside the sandbox.
Hardware decoding remains available; no driver or sandbox protection is disabled.

The helper uses a library basename plus `LD_LIBRARY_PATH` so normal profile
directories containing spaces work. It keeps the exact lexical library path,
including symlink names, for the dynamic loader's later name lookup. Paths that
cannot be represented safely in these loader variables produce a diagnostic
instead of being split or interpreted. Existing user loader entries survive.

Before resolving a subsequent launch, the launcher restores the original loader
environment. Disabling OpenH264 or entering `--ignore-flags` recovery therefore
removes the previous codec preload, and repeated relaunches do not accumulate it.
The preload is applied before the existing camera helper; its original loader
environment does not retain that helper from the preceding launch.

The initial fix used an already-present trusted codec. The 31 October follow-up
below adds acquisition for fresh profiles. The built-in software encoder does
not depend on this external decoder library.

## Validation

```sh
uv run python scripts/check_software_codecs.py /path/to/runtime/electron \
  --openh264 /path/to/libopenh264-2.5.1-linux64.7.so \
  --output out/software-codecs-check
bun run test
bun run lint
```

All six required native cases passed: default H.264, software H.264/VP8/VP9/AV1
on Wayland, and software H.264 on Xwayland. Each required case passed strict
codec preference checks, completed playback, sought successfully after draining
the clip, and verified decoded frame dimensions, timestamps and grayscale pixels.
Renderer `/proc` status reported `Seccomp: 2` and `NoNewPrivs: 1`; there were no
GPU exits. OpenH264 2.5.1 was loaded from a test directory containing spaces.

The no-preload control reproduced the H.264 failure. The separate HEVC software
attempt failed with no supported stream: this build has hardware HEVC support
but no software HEVC decoder. Those two diagnostic cases are recorded separately
from the six required passes. The forced hardware encoder crash is a separate
problem and is not fixed by this software decoder change.

Seven new loader tests use real tiny shared libraries to cover loading, symlink
names, user environment preservation, repeated relaunches, recovery/disable,
path precedence, missing files, and unrepresentable loader paths. All 276 Python
tests, 64 JavaScript tests and lint passed.

`check_graphics.py --launcher-codecs` applies the production helper; omitting
it preserves a direct-runtime control. `--strict-codecs` makes preferred codec
path errors fail the command. The graphics fixture now retains Media messages
and errors plus renderer sandbox state, and tests end-of-stream and seeking.
The preset matrix also uses the production codec helper.

These are synthetic checks on the local AMD RX 7900 GRE, with the existing
verified runtime. They do not establish behavior in Discord's native call stack,
on other GPUs, for unsupported bit depths/chroma formats, or in long calls.

## Fresh-profile acquisition follow-up, 31 October 2026

The previous preload fix assumed the library already existed. DPE did not acquire
it. A new profile reported the missing 2.5.1 library, and a real client launch
using Discord's asset directory left only `libopenh264-2.7.0-linux64.8.so` there.
The pinned OpenAsar flags still name 2.5.1. Sharing that directory therefore does
not preserve the decoder required by the tested runtime.

`integration/updater/openh264.py` now downloads the exact Cisco HTTPS archive
specified in `openh264.json`. The compressed archive and extracted library both
have pinned sizes and SHA-256 checksums. Extraction is bounded, installation is
atomic and fsynced, a profile lock serializes concurrent preparations, and a
transport interruption gets one retry. Failed verification never replaces the
previous bytes. A valid cache needs no network or rewrite.

The managed path is now
`{PROFILE_DIR}/dpe-codecs/openh264/libopenh264-2.5.1-linux64.7.so`, outside Discord's
asset cache. `{OPENH264_LIBRARY_PATH}` resolves there without modifying the user's
saved flags. The final resolved flags determine whether to prepare the codec.
Disabled defaults, recovery, a removed enable flag and custom library paths do
not trigger acquisition. Custom libraries remain user-managed.

The startup helper prepares the codec before the installed client is published
and executed. Quickstart preserves the user's actual offline selection separately
from its offline update check, so a missing codec is still prepared on an online
quickstart. Explicit offline mode requires a verified codec cache and reports
clear reconnect guidance when it is missing or damaged. It never preloads a
known-invalid managed cache. Local nonportable builds use the same helper.

The binary downloads directly to the end user's device and is excluded from
packages. The full Cisco binary notice is included, linked beside the essential
flags, with the provider attribution and disable/re-enable instructions.

Validation artifacts are under `out/validation-storage/openh264-reliability/`:

- `isolated-acquisition.json`: real HTTPS acquisition, unchanged offline reuse,
  and replacement of a corrupt test cache. The library hash is
  `d828a944d4d2bb64195ada89cf2cde9bc41733b1547d0788ef49fb8cb231b76f`, matching the
  earlier sandboxed decoder evidence.
- `decoding/software-codecs.json`: all six required native codec cases passed
  again, including software H.264 on Wayland and X11. Each required renderer had
  Seccomp 2 and NoNewPrivs 1. The diagnostic no-preload control failed as expected,
  and software HEVC remains unsupported.
- Acquisition regression tests cover concurrent starts, independent Discord cache
  cleanup, corruption, symlinks, interrupted transfers, permanent HTTP failure,
  bounded/truncated archives, offline mode and custom-path selection. Launcher
  tests cover resolved flags, recovery and real dynamic loading.

No runtime recompilation, account login or call was used.

Final startup checks also passed from the runtime-free release package, including
first launch and relaunch. `quickstart-repair/launch-report.json` verifies that a
corrupt managed codec is replaced before an online Quickstart. The
`offline-reviewed/launch-report.json` verifies an offline relaunch preserves the
valid file's inode and modification time. Both checks inspect the real browser
process mappings and require the verified managed library to be preloaded.
Discord rewrites `process.title`, collapsing `/proc/cmdline` and clearing the
original environment storage; the launch checker accounts for that representation.

All 432 Python tests, 177 JavaScript tests, lint and release-package validation
passed. The final launchable app is
`out/validation-storage/openh264-onboarding-reviewed/`.
