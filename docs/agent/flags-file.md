# Flags-file storage and automated codec checks

Follow-up on 2026-10-27 to the [preset implementation](launch-presets.md).
The user requested a shared flags file and automated video checks only.
No diagnostic button was added. Results are in
[flags-file and codec evidence](../evidence/flags-file-codecs.json).

The later [software codec fix](software-codecs.md) resolves the software H.264
failure reported below by loading its library before renderer sandbox startup.

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
