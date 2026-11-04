# Webcam flip with a matching preview

General → Webcam has one **Flip webcam horizontally** switch. It defaults off,
saves as `openasar.desktop.webcamFlip`, and applies live. The native camera frame
is flipped before it reaches either the self-preview or encoder. Discord's
additional CSS reflection on its self-video container is removed in both states,
so the preview orientation matches the outgoing video. There is deliberately no
independent preview setting that could disagree with the sent image.

## Native integration

`integration/camera/flip.c` is built into the existing camera-support shared
library. Its Node-API entry point resolves Node functions only when loaded as an
addon; the same library can still be preloaded into non-Node processes for format
probing. Building this helper now needs the Node.js development headers.

The voice-wrapper adapter installs before `VoiceEngine.initialize`. Packaging
identifies the single `ConvertToI420` call in the reviewed x86-64
`VideoCaptureImpl::IncomingFrame` function using symbols, disassembly and a
normalized instruction fingerprint. Runtime installation checks the module
checksum, executable ranges and original relative call target. Unknown layouts
leave native camera behavior unchanged and report that flipping is unavailable.

Only that camera call is redirected, using a nearby trampoline allocated without
replacing existing mappings. The proprietary file remains unchanged. Executable
memory is modified once, before the camera engine starts, and its original RX
protection is restored. Live changes update an atomic flag. After successful
conversion the adapter reflects the Y, U and V planes in place, respecting
strides, odd dimensions and rotation. Conversion failures pass through. Shared
converter calls from other capture and decoding paths remain unchanged.

The bridge reads the initial saved value and subscribes to main-process updates;
a delayed initial IPC reply cannot overwrite a newer save. Camera startup is not
triggered by the setting. This targets the native V4L2 webcam path, including
V4L2 virtual cameras; alternate camera backends such as NDI are not covered.

The preview rule targets the `video_` and `mirror_` class tokens on the same
container, mirrored `.media-engine-video` components, and `camera_` wrappers with
a direct native video child. The current public Canary renderer uses divs around
the video surface, rather than always reflecting the video element itself.
Source evidence is retained under `out/webcam-flip-research/`, including the
[Canary stylesheet](https://canary.discord.com/assets/883511.218e495706f2de12.css).

## Client receive and rendering review

The follow-up review traced the actual Canary JavaScript, staged native voice
module, and retained Electron/Chromium source. Extracted modules, native
disassembly and public asset scans are under `out/webcam-client-review/`.

- Native `RemoteVideoSource::OnFrame` (`0x3ebdd0`) forwards its decoded frame to
  `VideoSource::DeliverFrame`. The camera capture callback also forwards to this
  shared source implementation. The flip hook is before camera delivery and does
  not intercept the remote source or the shared renderer.
- `DirectOutputStream::FinishOnFrame` (`0x44a9a0`) passes the frame planes,
  dimensions, strides and rotation to Electron. The retained
  `electron/discord/discord_video_source.cc` wraps I420 planes unchanged and sets
  `VideoTransformation(rotation)`. Chromium's one-argument constructor explicitly
  sets `mirrored` to false. This path applies rotation, not a horizontal mirror.
- Client module `229209` connects `createDiscordStream(streamId)` to
  `addDirectVideoOutputSink(streamId)`. Module `206959` assigns that stream to the
  native video element's `srcObject`, with no additional reflection.
- Ordinary call tiles (`83982`) and video avatars (`614881`) request mirroring
  only when the participant's user ID equals the current user's ID. The shared
  renderer (`51092`) defaults mirroring off. Picture-in-picture (`806911`,
  `712577`) uses the same self-only condition. Received video is not mirrored by
  these components.
- The pre-call camera modal (`308229`) revealed a missed selector:
  [its stylesheet](https://canary.discord.com/assets/b496c4ad586ab431.css) reflects
  `.camera__19236`, which has no `video_` or `mirror_` token. The override now
  covers that wrapper when it contains a native video child. It also covers
  mirrored native components without a `video_` class, such as the zoom minimap
  component. The inspected minimap caller is screenshare-only and leaves its
  mirror prop false.

The main renderer asset reviewed was `web.8fb853a63180b897.js`; the voice module
SHA-256 was `2352578d855e5ff75c0433fe8ca0ffa6d599691a506a7908da3d0d6523cc91b8`.
The asset scans include the lazy call/popout and camera-modal dependencies, not
just the login page's initially loaded scripts. CSS-only chunk IDs have no
JavaScript URL; the final combined scan excludes those and completed without
fetch errors.

The expanded Chromium fixture reproduces the camera-preview mismatch with the
old selector (`settings-before/settings.json`). It checks the pre-call preview,
call tile, picture-in-picture and mirrored native minimap in both outgoing flip
states, while leaving remote video, screenshares and unrelated camera/mirror
classes alone. Those checks pass with the fix. The actual participant helper
(`110234`) also passes five self/remote camera, stream and activity cases in an
isolated JavaScript evaluation (`participant-mirror.json`).

The first full settings runs passed the webcam checks but exposed a separate
test timing issue: mod-install focus restoration is scheduled on the next
animation frame, after the action's busy state clears. The harness now waits for
the required focus before asserting it. No mod-install behavior was changed.
This remains code inspection and synthetic rendering validation; it does not
establish end-to-end delivery through a real call.

## Validation

The follow-up passes `bun run lint`, 559 Python tests, 265 JavaScript tests and
all 44 real settings-harness groups, including the expanded webcam checks.
Reports are `out/webcam-client-review/tests-final.log`, `lint-final.log` and
`settings-verified/settings.json`.

The initial checks passed: `bun run lint`, 554 Python tests and 265 JavaScript
tests. The matched-preview settings run passes all 44 groups:
`out/webcam-flip-matched-settings/settings.json`, with inspected normal and narrow
captures. `out/webcam-flip-matched-native/runtime.json` verifies saved startup
state and live on/off/on/off updates through the actual packaged wrapper, helper
and Electron IPC, using fake devices and a fresh profile.

The initial portable local build is `out/webcam-flip-ready/`. It reuses the qualified
Electron artifact from `/mnt/storage/dpe-qualified-artifact-20261101`; no Electron
compilation was started. The separate `out/webcam-flip-app/modules/discord_voice/`
directory supplies the staged native module for the runtime fixture. It is not
an installation into the user's Discord profile. This initial build predates
the follow-up camera-modal selector fix.

- Synthetic C tests cover 324 dimension/rotation combinations, odd chroma planes,
  padding and failed conversions. A compiled Node-API fixture checks all converter
  arguments through the trampoline, live toggles, duplicate installation,
  mismatched calls and an unaffected second conversion path.
- The actual downloaded native module accepts the hook; its real converter
  produces the expected unchanged and mirrored synthetic I420 planes. Reports:
  `out/webcam-flip-research/native-install.log` and `native-converter.log`.
- Bridge tests cover persistence, rapid startup saves, unsupported modules and
  checksum rejection. Controller and real Chromium DOM checks cover live state,
  matching preview orientation, unrelated video and changing class hashes.
- The real settings harness checks keyboard use, retained focus, tab remounts,
  narrow/zoomed layout and a long label, alongside its existing theme-token,
  reduced-motion and error-state checks. The fixture now establishes keyboard
  modality explicitly before checking the footer focus ring, and has a larger
  timeout for its expanded test matrix on software-rendered displays.

Use Xvfb with `WAYLAND_DISPLAY` unset and `XDG_SESSION_TYPE=x11`; leaving the host
Wayland environment active can make Electron use the desktop instead of the
isolated X server. The harness also needs a window manager for its native-focus
assertions. The disposable test window manager and launch commands are retained
in `out/webcam-flip-research/`.

No physical camera images, account login, calls or messages were used. Synthetic
buffers and an initialized engine with fake devices establish the native hook
and IPC behavior, not end-to-end call delivery. A real-call check remains manual.
