# Linux launcher, codecs and shortcuts

Use `~/.config/discord-canary-patched-electron.conf`, one launch flag per line.
For Stable/PTB replace `canary` with `stable`/`ptb`. `XDG_CONFIG_HOME` is honored.
Blank lines and comments beginning with `#` are ignored. This is a flag list,
not a shell script: do not include shell quoting, assignments or commands.

The launcher applies defaults, the older package-specific `-flags.conf`, this
channel-level file, then command-line arguments. Feature enable/disable lists
merge by feature name; the last setting wins. Other Chromium switches keep
their usual last-value precedence. `DPE_DEFAULT_FLAGS=0` disables our defaults.

Defaults in this build:

```text
--ozone-platform=wayland                 # when WAYLAND_DISPLAY is present
--ozone-platform=x11                     # otherwise; only one is selected
--enable-features=AcceleratedVideoDecoder,AcceleratedVideoDecodeLinuxGL
```

All of these are launcher settings, not changes to compiled Chromium defaults.
Do not copy the inline comments above into the config. Example overrides:

```text
# Force Xwayland for troubleshooting:
--ozone-platform=x11
# Disable the Linux GL decoding path:
--disable-features=AcceleratedVideoDecodeLinuxGL
```

On the local AMD RX 7900 GRE, synthetic playback used `VaapiVideoDecoder` with
platform decoding, and WebGL used hardware ANGLE/OpenGL. H.264, VP8, VP9 and AV1
software WebCodecs encoders each produced twelve frames. Forcing
`AcceleratedVideoEncoder` caused a GPU-process CHECK failure in
`CreateGpuMemoryBufferHandle` from the VA-API encoder's mappable shared-image
path, including with native GPU buffers enabled. Therefore it is **not enabled
by default**. It remains overridable using
`--enable-features=AcceleratedVideoEncoder` for deliberate testing on another
driver/runtime. The launcher does not override GPU blocklists, force Vulkan or
disable the GPU sandbox. `--ozone-platform=auto` also crashed this runtime;
explicit session selection avoids that path.

Discord's native voice/video module has its own codec stack. Chromium WebCodecs
results do not establish Discord-call hardware encoding. The native module
reported VA-API support and initial decode capabilities for H.264/H.265/AV1,
with VP8 encode/decode; that pre-call capability report is not a negotiated
call or hardware-encoder test. No live Discord call was made.

## Wayland shortcuts

The pinned `discord_utils` module implements the GlobalShortcuts portal itself,
including both press and release events needed by push-to-talk. Its shortcut
recording wrapper also handles Wayland keyboard events. Electron's
`globalShortcut` is a separate API; adding an Electron feature flag alone would
not repair Discord's native path.

The package now installs a reverse-DNS desktop file and gives the same identity
to the bootstrap, `DISCORD_PORTAL_APP_ID` and `CHROME_DESKTOP`:

```text
io.github.discord_patched_electron.DiscordCanary.desktop
```

Stable and PTB have separate identities. The local build emits the desktop file
next to `launch.sh`; copy it to `~/.local/share/applications/` to make the local
app resolvable by the portal. The prepared local Canary shortcut is installed
there already. Remove that user-local entry when switching to the system AUR
package, so it does not shadow the packaged entry.

A private D-Bus fixture verified native registry registration, a Ctrl+Alt+F12
preferred trigger, and `true`/`false` press/release callbacks. It does not bind a
real desktop key, change permissions or intercept typing. Reproduce with system
Python plus `dbus-python` and PyGObject in a Wayland session:

```sh
python scripts/check_shortcuts.py --runtime /path/to/runtime/electron \
  --utils /path/to/modules/discord_utils/discord_utils.node \
  --output /path/to/fresh-shortcut-check
```

The desktop's configured portal backend must implement GlobalShortcuts and the
user must grant any requested permission. Availability on this niri session was
confirmed; actual keyboard interaction on GNOME/KDE remains untested. Mouse
buttons or arbitrary unsupported combinations are not made global through an
X11 key-grabbing workaround.

References: [Electron's Linux shortcut identity requirements](https://www.electronjs.org/docs/latest/api/global-shortcut#usage-on-linux)
and the [GlobalShortcuts portal's press/release protocol](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.GlobalShortcuts.html).

## Storage compatibility

Electron 46 removes the synchronous `safeStorage` methods used by the pinned
desktop module. A small sidecar preserves Discord's synchronous renderer IPC
while awaiting the new asynchronous main-process API, including the structured
decrypt result. Locked/unavailable storage returns false/null; it never adds a
plaintext fallback. Older Electron methods remain supported.

`scripts/check_safe_storage.py` verifies real Electron IPC, invalid ciphertext,
null handling and decryption after relaunch. Only that isolated fixture selects
the basic password store and uses a synthetic string; the app keeps its normal
OS key store. Packaging rejects an unknown core IPC layout rather than silently
shipping an unpatched module.
