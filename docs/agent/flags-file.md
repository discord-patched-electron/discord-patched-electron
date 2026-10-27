# Flags-file storage and automated codec checks

Follow-up on 2026-10-27 to the [preset implementation](launch-presets.md).
The user requested a shared flags file and automated video checks only.
No diagnostic button was added. Results are in
[flags-file and codec evidence](../evidence/flags-file-codecs.json).

The later [software codec fix](software-codecs.md) resolves the software H.264
failure reported below by loading its library before renderer sandbox startup.

## Current compact Flags tab

The UI lives inside OpenAsar beside Settings and Theming, in a 560×760 window.
The preset selector is removed: Default and Custom were empty collections, not
useful optimization presets. The current active configuration remains editable;
inactive legacy configurations stay in file metadata for compatibility. Their
unused text does not block saving the active configuration. The unchanged stock
timer flags are retired.

A single Advanced section contains video decoding/encoding, NVIDIA VA-API,
acceleration bypass, software video decoding/rasterization, renderer selection,
the driver-fix bypass and Graphite. Only forced encoding and
Graphite have red icons and **Known to crash!** descriptions, backed by repeated
local GPU-process crashes. Other option icons are neutral, not safety claims.
The essential-flags heading retains its red startup-warning icon. Recovery help
appears in a blue information box immediately below the Flags tab bar, above the
form. Its command is selectable text in a code block, with no separator.
The other buffer, overlay, throttling and duplicate driver
controls are retired; their old flag lists are retained only to recognize
previously generated files without mistaking retirement for external edits.
Explicit raw custom flags remain available.

The NVIDIA option sets only `VaapiOnNvidiaGPUs`. It is off by default and links to
the required `nvidia-vaapi-driver` project through a fixed-URL, main-frame-only
IPC handler. Software decoding and `--disable-gpu` override it. Existing legacy
`nvidiaVaapi` selections remain readable; no encoding or blocklist bypass is added
when enabling this option. The expandable section uses explanatory warning text
without a `Breaking:` prefix. See [codec verification](codec-support.md) for the
NVIDIA hardware test gap and HEVC licensing investigation.

This follow-up passed 319 Python tests, 75 JavaScript tests, all 16 native UI
checks and `bun run lint`. The UI fixture also covers keyboard navigation, 320px
width, RTL and 200% zoom. Distribution validation passed, and the install guide's
dependency block matches the Arch package metadata. The local portable app at
`out/pr-review/app/` has the refreshed settings assets; its Electron, FFmpeg and
runtime licence files still match the verified runtime. Reports and screenshots
are under `out/nvidia-info-ui/`. NVIDIA playback was not tested on this AMD host.

The flags box shows only saved options and user overrides. Application identity and
profile-specific OpenH264 setup are resolved separately on every
launch, including after editing or clearing the box. `replaceDefaults` from the
older editor is retired; explicit saved overrides remain intact. The display
selector is also applied separately. CLI arguments are shown as comments and
are not captured as saved overrides.

Electron 46 [calls Chromium's platform selection][electron-platform]. The
[pinned Chromium implementation][chromium-platform] detects available displays
and uses `XDG_SESSION_TYPE` to break ties. Automatic adds no Ozone switch in a
normal Wayland or X11 session. It retains a Wayland override only when both
servers are available and session metadata would otherwise choose Xwayland.
Explicit Wayland/X11 selections and manual flags still take precedence.

Essential flags remain visible with a red flask icon. The Edit essential flags
checkbox unlocks the textarea; unchecking greys it out without shifting the layout
or disabling its launch flags. Reset restores both lists and Automatic display.
Save is disabled until the draft changes, including after saving or reverting
an edit. Drafts normalize the current settings into the same representation on
load and edit so legacy global/options storage does not create false changes.
The defaults
are `--class={DESKTOP_ID}`, `--enable-libopenh264`, and
`--openh264-library-path={OPENH264_LIBRARY_PATH}`. `{PROFILE_DIR}` is also supported.
Substitution happens only for saved flags, with a fixed variable allowlist and
no shell evaluation. Unknown names block saving and remain repairable in recovery.
Templates stay in the flags-file metadata, never expanded profile paths.
OpenAsar's `openH264Enabled=false` still suppresses codec setup.

The explicit autoplay switch is removed because [Electron already defaults to
no user gesture][autoplay]. The desktop class remains for X11 grouping, while
OpenH264 still needs the imported decoder switch and its cached library path.

[autoplay]: https://www.electronjs.org/docs/latest/api/structures/browser-window-options
[electron-platform]: https://github.com/electron/electron/blob/v46.0.0-alpha.1/shell/app/electron_main_delegate.cc
[chromium-platform]: https://github.com/chromium/chromium/blob/158.0.8114.0/ui/linux/display_server_utils.cc

When editing, existing outside-file flags are folded into each preset before
clearing that global section, preserving the effective behavior of inactive
presets. Invalid lines are not copied into inactive presets during repair.
Revision checks, backup writes, main-frame-only IPC and flags-free recovery remain.
The pinned OpenAsar source is unchanged; the served copy receives the tab and
local assets. Switching tabs keeps the draft, and restart is unavailable until
it is saved or discarded.

The layout follow-up passed 318 Python tests, 74 JavaScript tests and
`bun run lint`. Native Electron checks covered template editing, validation,
persistence, stable checkbox placement, disabled Save and essential textarea
states, unchanged submissions, legacy active configuration retention, keyboard
access, 320px layouts, longer German button labels, RTL and 200% zoom.
The refreshed portable test app passed a fresh-profile launch and relaunch to
Discord's login form under Xvfb/X11, reusing the verified Electron 46 runtime.
No account interaction or runtime compilation was needed. Earlier reports and
config backups remain in `out/flags-editor/`; current UI reports, screenshots and
test logs are in `out/flags-layout/`. The previous test command still uses the
refreshed `out/pr-review/app/launch.sh`.

The later risk-label follow-up passed 318 Python tests and 75 JavaScript tests;
native UI and lint reports are under `out/flags-risk-research/`. Native checks
cover section membership, exact crash labels, neutral icons for other options,
recovery separator/footer ordering, and conflicts between software fallbacks and
forced acceleration. Raw manual disable switches also update the controls.

The config header and window title now read **DPE OpenAsar Config**. A GitHub icon
and link at the top right open this project's repository in the system browser
through a fixed-URL, main-frame-only IPC handler. The pinned upstream HTML stays
unchanged. The header follow-up passed 318 Python tests, 75 JavaScript tests,
`bun run lint` and 15 native UI checks, including keyboard activation of the link,
header spacing at 320px, RTL and 200% zoom. The external-browser call is intercepted
by the fixture, so it does not launch the user's browser. Reports and screenshots
are under `out/config-header/`; the portable test app has the refreshed assets.

### Crash labels and further flag research

[Current evidence](../evidence/launch-flag-risks.json) records 13 fresh-profile
synthetic GPU/media cases with the verified Electron 46 runtime on the RX 7900 GRE.
Forced encoding crashed in both repetitions; forced Graphite crashed in both
repetitions before obtaining a WebGL context. The other nine cases passed,
including baselines before/after the crash cases, forced decoding, forced
acceleration and the software fallbacks. **Known to crash!** means a reproduced
local failure, not a claim of inevitable failure on every machine or workload.

Further candidates were checked against Chromium 158.0.8114.0 definitions and
consumers, rather than treating accepted switch names as proof of useful behavior:

| Candidate                                                                           | Decision and evidence                                                                                                                                                                                                                                                                                         |
| ----------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--disable-accelerated-video-decode`                                                | Added Software video decoding. The [renderer gate][codec-gates] disables GPU decoding. Native tests changed VA-API to software decoders, passed H.264 playback/seek and codec round trips, and retained hardware WebGL. H.264 needs the existing OpenH264 preload; this runtime has no software HEVC decoder. |
| `--disable-gpu-rasterization`                                                       | Added Software rasterization. [GPU feature evaluation][raster-gate] gives this switch precedence over forced GPU rasterization. Native Wayland and Xwayland probes reported `disabled_software` rasterization with working WebGL and playback. Combining it with software decoding also passed.               |
| `--disable-accelerated-video-encode` / `--disable-features=AcceleratedVideoEncoder` | The Linux renderer and [status calculation][compositor-gates] gate encoding on the feature. Explicit software encoding passed, but the baseline already uses software encoding. No redundant checkbox was added; removing Force video encoding restores the default.                                          |
| `--disable-accelerated-2d-canvas`                                                   | A genuine [canvas-specific override][compositor-gates], not a general acceleration toggle. Kept available as a manual diagnostic flag; no measured benefit justified another checkbox.                                                                                                                        |
| `--disable-gpu-compositing` / `--disable-gpu`                                       | Broader [diagnostic kill switches][diagnostic-switches]. They disable more than rasterization or video decoding; no new recommendation or performance claim.                                                                                                                                                  |
| `WebRtcPipeWireCamera`                                                              | A [separate camera backend][pipewire-camera], compiled conditionally and disabled by default. It is unrelated to GPU acceleration and is not assumed to control Discord's separate native voice/video stack. No camera test or new control was added.                                                         |

The new controls clear directly conflicting forced flags while preserving other
overrides. The feature/driver/rasterization bundle correctly becomes unchecked
if a manual software kill switch blocks it. Disabling a convenience checkbox
removes its requested flags rather than forcing the opposite policy.

[codec-gates]: https://github.com/chromium/chromium/blob/158.0.8114.0/content/renderer/render_thread_impl.cc
[raster-gate]: https://github.com/chromium/chromium/blob/158.0.8114.0/gpu/config/gpu_util.cc
[compositor-gates]: https://github.com/chromium/chromium/blob/158.0.8114.0/content/browser/gpu/compositor_util.cc
[diagnostic-switches]: https://github.com/chromium/chromium/blob/158.0.8114.0/content/public/common/content_switches.cc
[pipewire-camera]: https://github.com/chromium/chromium/blob/158.0.8114.0/media/capture/capture_switches.cc

### Preset decision

Rechecked Chromium 158.0.8114.0 rather than relying on old optimization recipes.
[Linux VA-API decoding and GL integration][media-defaults] are enabled by default;
hardware encoding is a separate opt-in. [IntensiveWakeUpThrottling][timer-default]
is also enabled by default. Turning background throttling off changes scheduling,
not GPU acceleration; [Electron documents its window-level control][autoplay].
The [raster-thread count][raster-threads] is already selected automatically and
clamped to 1–4. [Graphite's Linux device guard][graphite-guard] still rejects the
normal feature path, while its forcing switch remains an experiment.
Together with the existing [hardware test evidence](linux-gpu-flags.md), this
does not justify a general Performance or Battery preset. No new speed or power
benchmark is claimed. Individual troubleshooting overrides remain available.

[media-defaults]: https://github.com/chromium/chromium/blob/158.0.8114.0/media/base/media_switches.cc
[timer-default]: https://github.com/chromium/chromium/blob/158.0.8114.0/third_party/blink/common/features.cc
[raster-threads]: https://github.com/chromium/chromium/blob/158.0.8114.0/content/browser/gpu/compositor_util.cc
[graphite-guard]: https://github.com/chromium/chromium/blob/158.0.8114.0/gpu/config/gpu_finch_features.cc

### Layout review

| Severity | Location                                   | Before                                                      | After                                             | Why                                     |
| -------- | ------------------------------------------ | ----------------------------------------------------------- | ------------------------------------------------- | --------------------------------------- |
| MEDIUM   | `integration/updater/launch-settings.html` | Essential editing removed an entire section when unchecked. | The textarea stays in place and becomes disabled. | Keeps the controls at stable positions. |

Verified the 560×760 native window, 320px width, RTL, longer labels and 200% zoom.
No actionable layout findings remain. Approve.

The older sections below describe the previous multi-box UI. Earlier native UI
results are recorded in [compact tab evidence](../evidence/openasar-tabs.json).

## Storage and migration

The canonical file is `$XDG_CONFIG_HOME/discord-canary-patched-electron.conf`,
falling back to `~/.config`. It now owns preset selection, display selection,
every preset's editable text, independent decode/encode switches, backend,
experiments, and Additional flags. `settings.json` is only a migration source
for launch preferences; unrelated Discord/OpenAsar settings remain there.

The managed section starts with `# >>> OpenAsar launch presets v1`, then a
`# presets: ` comment containing compact schema-1 JSON. The metadata remembers
the controls and all presets. The following ordinary flag lines are the active
preset's generated options followed by its custom text. The section ends with
`# <<< OpenAsar launch presets v1`. Lines outside it appear verbatim in the
editor's Additional flags box and override the active preset. CLI flags win last.

When the active lines differ from the remembered controls/text, those actual
lines win: they become the active preset's text and its controls reset to
automatic/off with a notice. Other presets survive. This avoids silently
overwriting a manual change with remembered checkbox state. Outside comments
and literal values survive saves; shell syntax is never evaluated.

On the first normal launch, existing OpenAsar launch preferences migrate.
Additional flags combine the old global text, legacy package `-flags.conf`, and
existing canonical file text in their original precedence order. An existing
plain file gets a private backup before replacement. Preview alone never writes.
Migration write failures warn and retain the resolved launch behavior; they do
not prevent startup. Once a managed file exists, old preferences/package flags
are ignored. Saving in the editor clears the old launch preference keys; a stale
OpenAsar page cannot reintroduce them. Legacy files themselves are retained.

Saves use a same-directory temporary file, fsync, atomic replacement and an
advisory lock between app writers. Existing permissions and symlinks are
preserved; new files/backups are private. A content/target revision rejects a
stale window's save. The revision returned represents exactly our write, so an
immediate external edit is detected on the next save. The lock does not lock out
ordinary text editors; comparisons also check for edits before replacement.
**Reload from file** explicitly discards the draft and reloads current contents.

Malformed metadata is recoverable in the editor: valid raw lines remain visible
for repair and Save backs up the damaged original. Malformed flags must be fixed
before saving/restarting. The shell's `--ignore-flags`/`DPE_IGNORE_FLAGS=1` path
still bypasses the resolver and every file before parsing or migration.
`DPE_DEFAULT_FLAGS=0` skips defaults and the managed preset but retains Additional
flags and CLI arguments. Unreadable/oversized/non-UTF-8 files require external
repair; the early recovery launch itself still works.

## Automated video checks

`check_launch_presets.py` now writes each case to the real file and reloads it
before launching the fixture. Its ordinary matrix covers six preset/session
combinations, decode-only, encode-only, both, and recovery with saved unsafe
flags. `--codecs` adds VP8, VP9, AV1 and HEVC playback to H.264; `--full` retains
the individual backend/experimental option probes.

`tests/graphics/renderer.js` plays five actual video frames and keeps playback
alive for Chromium Media diagnostics. It encodes twelve synthetic frames for
each supported H.264/VP8/VP9/AV1 encoder preference, then decodes those bytes
with the encoder-provided codec configuration. Checks cover dimensions, unique
timestamps, frame count and grayscale pixel levels. The neutral ramp avoids
mistaking BT.601/BT.709 conversions for delivery failures. Every VideoFrame and
codec instance is closed after use.

The normal decoder selection must round-trip each successful encoder's output.
Explicit hardware/software decoder preferences are additional probes, with
unsupported capabilities distinguished from initialization/output errors.
Their failures remain in `codecPreferenceFailures`; `codecPreferencesPassed`
is false whenever an advertised path fails. `check_graphics.py --strict-codecs`
also makes these failures affect the command's exit status. GPU crashes always
fail the individual graphics case. Experimental matrix cases are reported
separately from the required default cases.

## Results and limits

- 269 Python and 64 JavaScript tests passed; `bun run lint` passed.
- Native editor checks passed file persistence, migration, stale-page and
  external-edit protection, reload, repair/backup, independent controls, recovery
  relaunch, keyboard focus, accessibility names, 320px, RTL and 200% zoom.
- All 11 required runtime cases passed, plus decode-only: 12 of 14 cases total.
  All five clip codecs played. H.264/VP9/AV1/HEVC used VA-API; VP8 used software.
  Default H.264/VP8/VP9/AV1 encode/decode round trips passed.
- Encode-only and combined encode/decode reproduced the existing GPU crash;
  these remain explicit experiments. Software H.264 decode advertises support
  but fails initialization. Strict mode correctly exited 1 for that limitation.
  Hardware VP8 decode and default hardware encoding reported unsupported.

Runtime: verified Electron 46.0.0-alpha.1 / Chromium 158.0.8114.0 on the local
AMD RX 7900 GRE. No rebuild, account use, real call, physical camera capture or
desktop screenshot was needed. These checks do not establish behavior on other
GPUs, Discord's native call stack, or color fidelity/power/performance gains.

### Advanced section and browser links follow-up

The software decoding hint no longer asks users to supply OpenH264; startup
already configures it. All experimental controls now share one Advanced section.
Both project links use Electron's normal `shell.openExternal` through a fixed
allowlist and main-frame-only IPC. IPC/opening errors appear in an alert.

The reported browser failure was caused by the fresh-profile command overriding
`XDG_CONFIG_HOME`, which hid the user's browser association and selected a broken
DMS handler instead of Zen. The custom `xdg-open` wrapper and environment cleanup
were removed after tracing this. The corrected command preserves desktop config.
See [external-link verification](external-links.md) for the process trace and
actual browser-load evidence. The native settings fixture passes all 17 checks,
including the real Electron-to-opener process boundary, fixed destinations,
keyboard activation and rejection of arbitrary URLs.
