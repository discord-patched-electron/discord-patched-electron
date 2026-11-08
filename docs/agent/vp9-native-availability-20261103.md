# VP9 native availability versus Windows

The retained Linux `discord_voice.node` does not merely omit a capability flag.
Its VP9 implementation is compiled out. Disassembly shows:

| Function                              | Address    | Behavior                         |
| ------------------------------------- | ---------- | -------------------------------- |
| `webrtc::SupportedVP9Codecs(bool)`    | `0x92b5c0` | Returns an empty vector          |
| `webrtc::SupportedVP9DecoderCodecs()` | `0x92b5e0` | Returns an empty vector          |
| `webrtc::CreateVp9Encoder(...)`       | `0x92b600` | Calls `UnreachableCodeReached()` |
| `webrtc::VP9Decoder::Create()`        | `0x92b630` | Returns null                     |

This matches the disabled `RTC_ENABLE_VP9` branches of the
[upstream WebRTC implementation](https://webrtc.googlesource.com/src/+/refs/heads/main/modules/video_coding/codecs/vp9/vp9.cc).
Advertising VP9 by changing JavaScript capabilities would not create an encoder
and could send execution into the fatal stub. No such capability override was
added. Supporting this native path needs a working encoder/decoder implementation
and factory integration, not a Linux boolean or an Electron flag.

The examined Linux module SHA-256 is
`72096efc87a407b975e9913477d387e2de9a1f1028575c1f97a22365bacb007c`.
Raw disassembly and machine-readable findings are retained in
`out/vp9-windows/linux-vp9-stubs.txt` and `report.json`.

## Windows comparison and limits

The current official Windows Canary manifest was fetched read-only, and its
1.0.1292 x64 voice package was downloaded with the manifest checksum verified:

- [Official voice package](https://canary.dl2.discordapp.net/distro/app/canary/win/x64/1.0.1292/discord_voice/1/full.distro)
- Package SHA-256: `3320d18d7acfa0f4a3c13bfb04f74cfc8e0e94a55875b10abd451e8b3a72ceec`
- Module SHA-256: `6803596ec541909fa71a502d71dcf7d95b5c62f4cbb02d0d09bfbecaef4d7f3b`

That PE contains generic VP9 RTP/parser strings. These do **not** prove a working
VP9 encoder. No Windows runtime was available for an encoder/capability check,
and its matching PDB was unavailable from Discord’s public symbol server.
Consequently this investigation does not establish Windows native VP9 encoding
or assert its absence from strings alone.

[Discord’s published streaming overview](https://discord.com/blog/how-it-all-goes-live-an-overview-of-discords-streaming-technology)
names VP8/H.264, plus HEVC/AV1 encoders on selected platforms. That historical
article does not prove the exact capabilities of a current Windows build.
Discord Web/Chromium WebRTC and WebCodecs may support VP9 independently; their
successful software tests do not show support in the separate native voice module.

Only public packages and source were read. No Windows binary was executed,
account accessed, call made, installed module changed, or runtime restarted.
There was no safe flag-only VP9 fix to implement. Parent owns final documentation
lint and index integration.
