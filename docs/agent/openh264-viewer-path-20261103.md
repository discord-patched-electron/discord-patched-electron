# Fresh-profile H.264 viewer failure, 2026-11-03

The alt profile received valid 1920×1080 H.264 keyframes but repeatedly failed
decoder initialization with `avcodec_open2 error: -1313558101`. Native decoder
parameters requested `discord_asset_cache/openh264/libopenh264-2.5.1-linux64.7.so`,
while Discord populated its cache with 2.7.0 instead. The pinned runtime uses
the verified Cisco 2.5.1 library managed separately by DPE.

Copying the verified library into Discord's cache did not persist: after the
user restarted, that file was absent again. Do not use this as a lasting fix.
The viewer's process maps confirmed that the correct managed 2.5.1 library was
already preloaded. The requested decoder path differed from that loaded path.

The correction belongs at the incoming native decoder-configuration boundary:
enabled H.264 OpenH264 decoding must refer to the actual preloaded library,
without changing hardware preference, codec negotiation, encoder configuration
or the contents of Discord's independently managed cache. A native decode check
must exercise the stale incoming path, rather than supplying the correct path
directly as earlier synthetic checks did.

Implemented in the native transport wrapper. The resolver identifies the unique
preloaded managed library from process maps, including when the environment path
is absent. Deleted or ambiguous mappings are rejected. Only enabled H.264
decoders with missing/disposable-cache paths are redirected; explicit custom
paths and hardware preference are preserved.

Native validation deliberately supplied a nonexistent Discord-cache path with
the managed Cisco library preloaded and the environment hint absent. The native
decoder selected the managed path and decoded 94 distinct frames without any
`avcodec_open2` errors. Evidence:
`out/quality-regression/openh264-stale-cache/decoder-path-proof.json`.
This proves decoder initialization, not overall streaming throughput.

Full validation: lint passed, 676 Python tests passed, 408 JavaScript tests
passed and two skipped. The candidate includes the updated wrapper; no helper
or Electron rebuild was needed. Live viewer playback remains to be confirmed
after the user restarts the alt instance.
