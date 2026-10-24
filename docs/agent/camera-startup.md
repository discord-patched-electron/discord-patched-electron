# Native webcam startup

The delay reproduced with the physical Logitech UVC webcam and the installed
Canary 1.0.2360 native voice module. Discord's log spent 4.96–5.39 seconds in
`DeviceInfoV4l2::FillCapabilities` before capture started. Its legacy WebRTC loop
tries a matrix of pixel formats and resolutions, including formats the camera
cannot supply. The kernel adjusts unsupported requests to supported formats;
this loop can then count the adjusted format as the originally requested one.
The matching implementation is visible in [WebRTC's V4L2 device code](https://webrtc.googlesource.com/src/+/refs/heads/main/modules/video_capture/linux/device_info_v4l2.cc).

The fix checks the driver's [advertised formats](https://docs.kernel.org/userspace-api/media/v4l/vidioc-enum-fmt.html)
before those expensive probes. `libdpe-camera-probe.so` skips unadvertised formats
only when `VIDIOC_TRY_FMT` originates inside the exact `FillCapabilities` function
of the packaged `discord_voice.node`. Package preparation derives the function
range from its ELF symbols and records the module hash. Other callers, supported
formats, other request types and failed/unknown enumeration keep upstream behavior.
There is no cached device state to become stale after hotplug or descriptor reuse.
The helper never changes `S_FMT`, starts a camera itself, or keeps it running after
Discord stops it. It does not alter camera resolution, frame rate or background effects.

This is deliberately scoped to the capability loop: [normal V4L2 format negotiation](https://docs.kernel.org/userspace-api/media/v4l/vidioc-g-fmt.html)
allows drivers to adjust unsupported requests, and other callers still receive
that behavior. Unknown voice layouts fail package preparation for review. The
proprietary binary is not rewritten, and no Electron or Chromium rebuild is needed.
`DPE_CAMERA_FAST_PROBE=0` disables the helper for troubleshooting.

## Physical measurements

Two alternating upstream/patched runs used the same installed Canary module and
bundled Electron 44.9.3, in a local test app on Wayland:

| Run | Upstream probe | Patched probe | Upstream first frame | Patched first frame |
| --- | -------------: | ------------: | -------------------: | ------------------: |
| 1   |        5.382 s |       0.652 s |              7.770 s |             1.736 s |
| 2   |        4.964 s |       0.654 s |              6.047 s |             1.736 s |

Both paths produced a 1280×720 frame. Camera warm-up varies; the helper removes
the unnecessary probing, not the device's own startup latency. These measurements
verify this physical camera and native module, not every camera or an end-to-end
Discord call. Images were neither displayed nor saved, and microphone capture used
the native fake-audio option. The normal client/profile was not opened by the test.
[Retained evidence](../evidence/camera-wayland-local.json) records hashes, runtime
versions and each measurement.

To reproduce with an explicitly selected camera, supply an installed Discord
executable and its voice module:

```sh
.venv/bin/python scripts/check_camera_startup.py \
  --runtime /path/to/DiscordCanary \
  --voice /path/to/discord_voice/discord_voice.node \
  --device 'UVC Camera (046d:0825)' \
  --output out/camera-check
```

The script copies the runtime/modules into a fresh test directory, builds the small
helper, and compares startup twice. Use an idle camera. It creates only a localhost
test voice connection with fake audio, never a Discord call or real microphone,
and saves only timings, dimensions and diagnostic logs. CI uses a fake driver to
check caller scoping, supported-format passthrough, missing/failed enumeration,
changed capabilities, multi-plane passthrough and explicit disablement.

Physical webcam frames must never be serialized, displayed, recorded, uploaded,
or included in screenshots. The fixture reads dimensions from each in-memory
frame and discards it. Only measurement metadata belongs in retained evidence;
physical-camera test directories are never CI upload inputs. Do not take desktop
screenshots while a physical-camera test is running.

The compiled Electron 46.0.0-alpha.1 runtime with packaged Canary 1.0.2357 also
passed the physical test: **8.37 seconds upstream versus 2.33 seconds patched**.
The capability probe fell from 5.42 to 0.66 seconds. The report verifies the
compiler artifact's version and keeps metadata only; see
[compiled-prerelease evidence](../evidence/prerelease-runtime-local.json).

## Voice interruption while the camera initializes

The pinned wrapper enabled `asyncVideoInputDeviceInit` only on Windows, although
the Linux native module also implements the asynchronous control path. Synchronous
initialization blocks the voice engine's command queue while opening/probing the
camera. The package now enables that existing path on Linux too.
`DPE_CAMERA_ASYNC_INIT=0` restores synchronous behavior for comparison; this is
independent of `DPE_CAMERA_FAST_PROBE` and needs no Electron recompilation.

With fast probing enabled in both runs, the largest native engine heartbeat gap
fell from **1,130 ms to 25.3 ms**, and first-frame time from **3,455 ms to 1,739 ms**
on the physical webcam. Use `check_camera_startup.py --compare-async` to repeat.
This directly verifies removal of the engine-thread stall. The fake-audio
loopback sent only its ten startup silence packets, so it did **not** establish
sustained voice transmission during capture; a real-call continuity check remains
unperformed. No camera images/video were displayed or saved.
See [measurement metadata](../evidence/desktop-fixes-local.json).

## Build and packaging

The standard GitHub checks compile this small x86-64-v3 library and retain it with
`camera-support.json`. AUR downloads the precompiled, checksum-pinned binary.
The helper's source/build identity belongs to package integration, separate from
Electron's compiler key. A helper fix therefore reuses the existing Electron
runtime and objects. See [packaging](packaging.md#independent-camera-support-binary).
