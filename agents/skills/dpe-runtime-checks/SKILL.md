---
name: dpe-runtime-checks
description: Select and run isolated DPE desktop, native video/Krisp, frame-capture, application-audio or camera-startup checks using verified runtimes and exact client snapshots.
---

# Validate a DPE runtime or integration

Read [runtime validation](../../../docs/agent/runtime-validation.md) and
[CI check selection](../../../docs/agent/ci-setup.md#check-selection). Choose
fixtures by the contract changed; do not equate a broad unit pass with native
media or desktop validation. Run commands from the repository root.

## Bind checks to the candidate

Use a verified existing runtime and record its provenance/hash, the exact client
snapshot, prepared module identity and working-tree adapters. Supply explicit
candidate inputs where supported; the bootstrap lock may test an older client.
Keep every run in a fresh `out/` directory. Preserve failed reports and logs.

For native video/bridge and offline Krisp:

```sh
uv run python scripts/check_runtime_clients.py \
  --runtime /path/to/extracted-runtime \
  --provenance /path/to/artifact-provenance.json \
  --clients /path/to/clients.lock.json --discord canary \
  --output out/runtime-check
```

Omitting `--runtime` intentionally tests the bundled baseline instead. Do not
report that as a custom-runtime pass. Check current enabled channels and flags.

## Choose additional coverage

| Changed behavior                                           | Fixture / maintained guide                                                                              |
| ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| Bootstrap, login, module API, popouts and restart          | `scripts/check_desktop_launch.py`, [release packaging](../../../docs/agent/releases.md)                 |
| Native FrameCapture ownership and teardown                 | `scripts/check_frame_capture.py`, runtime guide                                                         |
| Native audio adapter and Discord picker/settings contracts | `scripts/check_app_audio.py`, [audio picker](../../../docs/agent/audio.md#picker-and-ownership)         |
| Portal video plus selective application audio              | `scripts/check_wayland_audio.py`, [Wayland audio](../../../docs/agent/audio.md#routing-and-teardown)    |
| Camera startup and native bridge                           | `scripts/check_camera_startup.py`, [camera startup](../../../docs/agent/native-media.md#camera-startup) |
| DPE settings and presentation                              | [settings skill](../dpe-settings-ui/SKILL.md)                                                           |
| Codecs, framing, GPU handoff and capture lifecycle         | [capture skill](../dpe-capture-checks/SKILL.md)                                                         |

Read each selected script's current `--help` before composing its invocation.
Audio changes need selection, cancellation, source replacement and route cleanup;
test both PipeWire-Pulse and real PulseAudio when the routing contract affects
both. Preserve PID/start-time/UID identity and explicit selection on ambiguity;
never infer automatic authority from a window title or focused app.

Use fresh profiles and owned synthetic windows/media/private audio services.
Do not log in, send messages or make unattended calls. Do not save physical
webcam images/video or screenshot the desktop during physical-camera tests.
Keep normal profiles, audio services and unrelated processes untouched.

Report exact pass/fail evidence and what it establishes. Synthetic sender/receiver
success, a fixture compile or one GPU's result does not establish real-call
quality, all capture backends or Intel/NVIDIA/Windows parity. Missing libraries
or unsupported display/decoder paths remain explicit limits, not silent skips.
