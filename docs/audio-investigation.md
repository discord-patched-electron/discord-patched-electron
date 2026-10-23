# Native application-audio investigation

Static inspection on 23 October 2026 found an existing Linux application-audio
path in all three locked `discord_voice.node` binaries. No module was loaded,
no client was launched and no personal profile or microphone was accessed.
The binaries differ; reports identify each by its own SHA-256:

- [Stable](evidence/stable-voice-interface.json)
- [PTB](evidence/ptb-voice-interface.json)
- [Canary](evidence/canary-voice-interface.json)

## Observations

| Native boundary | Observed field references or calls |
| --- | --- |
| Connection `SetTransportOptions` | `soundsharePid`, `soundshareLoopback`, `soundshareDiscardRearChannels` |
| `SetDesktopSourceWithOptions` | `sourceId`, `type` |
| `SetGoLiveDevices` | `videoInputDeviceId`, `audioInputDeviceId` |
| PulseAudio sink identification | `application.process.id` |
| PulseAudio client identification | `application.process.id`, `pipewire.sec.pid` |
| PulseAudio stream monitor | `pa_stream_new`, `pa_stream_set_monitor_stream`, `pa_stream_connect_record` |
| PulseAudio attachment | Enumerates sink inputs through `pa_context_get_sink_input_info_list` |

These names are referenced by the corresponding native functions, rather than
merely appearing somewhere in the binary. The existing JavaScript wrapper
forwards connection transport options, source options and Go Live devices to
the native instance. In Stable's monitor function, disassembly shows the monitor
selection call before the recording connection call. PulseAudio documents that
this API restricts recording to a specific playback stream when connected to a
monitor source. [PulseAudio API](https://www.freedesktop.org/software/pulseaudio/doxygen/stream_8h.html)

The evidence supports testing the existing native path first. It does **not**
prove that the UI supplies a usable PID, that loopback is disabled for window
sharing, that PID namespaces or child processes match, or that the server honors
the requested stream isolation. The `SetGoLiveDevices` device fields are another
possible boundary; their interaction with video and microphone capture remains
unverified. No audio patch or helper is enabled on this evidence alone.

## Next experiment

Use an isolated test client to record only the source selector and the allowlisted
soundshare fields at those wrapper boundaries. Compare window and monitor sharing,
audio enabled/disabled and source switches. Do not serialize the full transport
options object: it also contains connection/encryption-related settings.

Against an isolated PulseAudio/PipeWire session, produce different tones in two
test applications. Compare the supplied soundshare PID with their playback-stream
properties and the chosen video source. Test correct PID with loopback disabled,
wrong PID and PID zero, plus child-process and namespace cases. Verify captured
audio and microphone independence. Only then choose a small wrapper correction,
a checksum-gated native fix or a venmic adapter. Preserve failure/cleanup behavior
and never silently substitute system audio when application identity is unknown.

To reproduce the static report from a checksum-verified extracted module:

```sh
.venv/bin/python scripts/inspect_voice.py /path/to/discord_voice.node \
  --report out/voice-interface.json
```

The inspector uses ELF symbols, instruction references and `objdump`; it never
executes the input. Reports explicitly keep runtime and application-isolation
validation false. Missing or changed symbols remain visible in the report.
