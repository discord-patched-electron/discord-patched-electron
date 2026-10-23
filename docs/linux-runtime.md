# Linux runtime checks and defaults

Electron 38 and newer choose native Wayland automatically when
`XDG_SESSION_TYPE=wayland`; the old `ELECTRON_OZONE_PLATFORM_HINT` is gone.
The launcher preserves that upstream selection. Use `--ozone-platform=x11` in
its flags file for an explicit X11 fallback. Sources:
[Electron 38](https://www.electronjs.org/blog/electron-38-0) and
[Electron's Wayland explanation](https://www.electronjs.org/blog/tech-talk-wayland).

Discord already defaults hardware acceleration on. The imported Chromium code
also defaults VA-API decoding and its Linux GL integration on when built with
VA-API support. We do not override users' acceleration settings, driver checks,
NVIDIA safeguards, Vulkan, or experimental encoding features. Discord's native
voice/video encoders are separate from Chromium's browser video encoder; a
Chromium flag alone does not establish native screen-share hardware encoding.

A local baseline probe on niri, with Radeon RX 7900 GRE and Mesa 26.2.4, verified
native Wayland without an ozone override, AMD hardware WebGL rendering, and H.264
playback through `VaapiVideoDecoder` (`kIsPlatformVideoDecoder=true`). This was the
bundled Electron 44.9.3 baseline; a new compiler artifact must pass its own check:

```sh
.venv/bin/python scripts/check_graphics.py /path/to/runtime/electron \
  --output /path/to/fresh-graphics-check
```

The probe uses a generated video test pattern, creates a temporary visible test
window, and records GPU/decoder metadata. It never opens a camera or microphone.

## Direct local Canary build

The AUR and local build share the same bootstrap, native modules, Krisp, audio
adapter and camera helper. The packaged bootstrap disables the legacy host-update
block for locally installed modules, matching its existing module-update policy.
This prevents a newer Discord host release from blocking login while a new package
is being prepared. It leaves the actual version unchanged. The established
[Flatpak updater policy](https://github.com/flathub/com.discordapp.Discord/blob/master/disable-breaking-updates.py)
uses Discord's host/module skip settings for the same problem; our change applies
at package preparation and does not rewrite user settings on every launch.

After `fetch_reference.py` retrieves and verifies a successful compiler artifact:

```sh
.venv/bin/python scripts/build_local_app.py --artifact /path/to/verified-artifact \
  --discord canary --cache /path/to/canary-archive-cache \
  --output /path/to/canary-test-build
.venv/bin/python scripts/check_desktop_launch.py /path/to/canary-test-build \
  --output /path/to/fresh-launch-check
/path/to/canary-test-build/launch.sh
```

The local folder embeds its absolute path; rebuild it if moved. It requires the
same runtime dependencies as the Arch package and uses a separate
`discord-canary-prerelease-patched-electron-bin-local` profile root. No root
installation is needed. Chromium's user-namespace sandbox remains enabled.
`--bundled-baseline` can test packaging before compilation finishes, but such an
output is explicitly labelled as a baseline and cannot qualify a custom runtime.

The launch check loads the real login page with a new empty profile, checks its
email/password fields, then closes the app. It does not sign in, make calls, send
messages or capture screenshots, microphones or webcam images. A passing launch
check does not establish real-call quality on every compositor/GPU.
