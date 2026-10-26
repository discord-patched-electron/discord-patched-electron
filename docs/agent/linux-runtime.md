# Local runtime checks

Use these checks when changing the runtime or Linux integration. They reuse a
verified Electron artifact and fresh profiles. For releasable portable packages,
follow [release packaging](releases.md); the local build below is a development
fixture with a fixed path and separate profile.

## Graphics

The launcher explicitly selects Wayland when `WAYLAND_DISPLAY` is present and
X11 otherwise. `--ozone-platform=x11` provides an Xwayland fallback. Hardware
decoding is enabled where supported; driver checks, GPU blocklists and the sandbox
remain in effect. See [launcher details](linux-launcher.md).

The bundled Electron 44.9.3 baseline on niri, Radeon RX 7900 GRE and Mesa 26.2.4
passed native Wayland, AMD hardware WebGL and H.264 `VaapiVideoDecoder` playback.
Each new compiler artifact needs its own check:

```sh
uv run python scripts/check_graphics.py /path/to/runtime/electron \
  --output /path/to/fresh-graphics-check
```

The probe shows a generated video pattern and records GPU/decoder metadata.
It does not open a camera or microphone. Browser codec results do not prove
hardware encoding in Discord's separate native voice/video stack.

## Direct local Canary build

After `fetch_reference.py` retrieves and verifies a successful compiler artifact:

```sh
uv run python scripts/build_local_app.py --artifact /path/to/verified-artifact \
  --discord canary --cache /path/to/canary-archive-cache \
  --output /path/to/canary-test-build
uv run python scripts/check_desktop_launch.py /path/to/canary-test-build \
  --output /path/to/fresh-launch-check
```

The non-portable folder embeds its absolute path; rebuild it if moved. It uses
`discord-canary-prerelease-patched-electron-bin-local` as its profile directory
and needs the same system dependencies as the app. No root installation is needed.
Chromium's user-namespace sandbox stays enabled.

For an interactive launch of this test build:

```sh
/path/to/canary-test-build/launch.sh
```

`--bundled-baseline` can test integration without a compiled custom runtime.
That output is labelled as a baseline and cannot qualify a release.

The automated launch check opens the real login page with an empty profile,
checks its email/password fields and closes the app. It does not sign in, send
messages, make calls or capture screenshots, microphones or webcam images.
A pass establishes startup, not live-call quality.

## Legacy updater policy

The non-portable fixture disables Discord's legacy host-update block so a newer
host release does not prevent its tested client from reaching login. It keeps the
actual version unchanged. This follows the same principle as the
[Flatpak updater policy](https://github.com/flathub/com.discordapp.Discord/blob/master/disable-breaking-updates.py).
Published portable packages use the [approved module updater](client-updater.md).
