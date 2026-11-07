# Diagnostic coverage audit — 5 November 2026

This audits project-owned subsystems in dependency order. The bug-report export
collects existing text logs, safe state and live status; it does not activate an
update, start a capture, open a camera, prompt for permissions or change audio
routing. `coverage.json` maps subsystems to evidence; `manifest.json` records
missing sources, command failures, limits and redaction exclusions.

## Subsystem disposition

| Subsystem                                        | Evidence collected or added                                                                                                                                                                                                     | Limits                                                                                                                                                                                |
| ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Installed application loader                     | `bootstrap-loader.log` records selection, handoff and fatal errors; `dpe-application/*/state.json` supplies current/pending/previous/rejected activation state.                                                                 | Loader failures before profile resolution cannot create a profile log. New loader logging requires an updated installed loader.                                                       |
| Shell launcher and flags                         | Bounded stdout/stderr relay preserves terminal output and launcher PID; runtime snapshot includes allowlisted effective Chromium switches. Explicit Chromium `log-file`, `log-net-log` and `CHROME_LOG_FILE` are collected.     | Arbitrary argv, account URLs, environment variables and raw flag/settings files are excluded. New launcher logging requires a refreshed launcher, not a helper-only dev sync.         |
| Runtime bootstrap                                | Runtime bootstrap progress/error events and launcher stderr; selected runtime metadata and current pointers.                                                                                                                    | Historical stderr from an older launcher may not exist.                                                                                                                               |
| Application/client/runtime update preparation    | Python progress persists to `logs/dpe/updater.log`; main-process runner records start/progress/completion, cancellation and stderr. Current approval pointers, check-throttle timestamps and generation metadata are collected. | Old updater Python in an existing managed version stays frozen; its runner can still retain progress/stderr after helper restart.                                                     |
| Activation/recovery/native updater compatibility | Activation journal, read-only selected `installer.db` DPE/host rows, active-generation manifests, helper-file inventory and profile/temp storage availability.                                                                  | No raw database, install ID, backup database or activation replay is exported/performed.                                                                                              |
| OpenH264/bootstrap/native compatibility          | Launcher stderr, updater events, runtime/client metadata, loaded native-library paths and encoder/capture status.                                                                                                               | Silent native behavior needs explicit native instrumentation; no new codec or driver is loaded for export.                                                                            |
| Main process                                     | Rotating runtime log retains Node stdout/stderr when not already relayed, startup/exit and `uncaughtExceptionMonitor`.                                                                                                          | Exception and stream behavior is preserved; no handler suppresses otherwise fatal errors.                                                                                             |
| Discord renderer/preload                         | Trusted Discord console output, load errors, unresponsive/crash events and existing renderer logs.                                                                                                                              | No arbitrary page console, URL, window title, DOM, account store or screenshot is exported. Past unsaved console output is unavailable.                                               |
| DPE Settings/Welcome/startup windows             | Console/error events for trusted local `dpe-splash` hosts; renderer/process failure events.                                                                                                                                     | Public presentation shells and unrelated pages do not gain diagnostics IPC.                                                                                                           |
| GPU/utility processes                            | Process metrics, GPU features/info, native libraries, child-process exit reasons, graphics device/driver permissions, `lspci` and accessible GPU kernel journal errors.                                                         | Commands do not elevate privileges; unavailable kernel journals appear in the manifest.                                                                                               |
| Discord native modules                           | Active host modules, published `app-*/modules` views and native module logs, including `discord_utils.log`. Known module links resolve once; ordinary tree symlinks are skipped.                                                | No binary module payload, source archive or unrelated account/cache tree is copied.                                                                                                   |
| Voice/WebRTC/network                             | Existing Discord/RTC/voice/renderer logs and explicitly configured Chromium logs; live encoder/decoder, connection/capture counters through the existing restricted stats bridge.                                               | No RTP/audio/video bytes, encryption keys, SDP, cookies or credentials. Unrecorded WebRTC/media-internals dumps cannot be reconstructed. Debug-only traces are not enabled by export. |
| PipeWire/PulseAudio and audio helper             | Existing helper warnings/errors, whitelisted audio-helper error output, native/module logs, service status and scoped audio service journals. `pactl --version` runs without connecting to or autospawning an audio server.     | Unlabelled helper stderr intentionally discarded by an audio transport cannot be recovered. Existing trace mode remains explicit; export does not alter routing.                      |
| Direct Wayland/KMS                               | Native worker stderr is drained/persisted; allowlisted ready/error/end counters are recorded. Permission and live capture snapshots exclude source titles.                                                                      | No polkit prompt, monitor acquisition, preview or frame read is initiated. Root-owned helper cores remain outside the current-user core query.                                        |
| Game/Vulkan/OpenGL capture                       | Broker/producer stderr and allowlisted lifecycle counters, native errors, live handoff/fallback status and module/runtime logs.                                                                                                 | Game names, broker source catalogs, tokens, thumbnails and GPU image buffers are excluded. The exporter does not collect an unrelated game's full log.                                |
| Capture/encoder/decoder performance              | Live status at export plus safe stats every 15 seconds while actively sharing/watching, using the existing restricted status bridge.                                                                                            | Stats are counters, dimensions, codecs, rates and fallback reasons, not media. Short sessions can end before the periodic sample.                                                     |
| Camera                                           | Bridge/load errors, desktop camera-flip support state and camera node permissions.                                                                                                                                              | Device nodes are not opened. No camera image, preview, recording or hardware serial is saved.                                                                                         |
| Desktop/tray/popouts/spelling/safe storage       | Main/renderer errors, selected normalized desktop preferences, spellcheck enable/language/count state and loaded native libraries.                                                                                              | No custom CSS/JS, tray images, window positions/titles, dictionary files or encrypted account data is copied.                                                                         |
| Vencord/Equicord/mod installation                | Default/custom mod log directories, bounded setup stderr, setup progress/result errors and DPE mod-operation metadata.                                                                                                          | No mod settings, plugin code, account settings, downloads or backups.                                                                                                                 |
| Crash handling                                   | Recent local minidump/ELF summaries and accessible systemd metadata/stack frames, scoped by current UID and known current/previous app/helper executables.                                                                      | Raw memory is excluded. Generic Python/game cores and other users' processes are not swept into the archive.                                                                          |

## Collection and privacy behavior

- The collector prioritizes DPE and voice/RTC logs and deduplicates traversed
  directories/files across active and published module views. Dedicated log
  directories include text files without a `.log` extension and bounded gzip logs.
- Selected updater/installation state, system hardware inventory and live runtime
  metadata are separate JSON artifacts. Arbitrary settings and databases remain
  excluded. Service diagnostics are read-only; collecting stats does not start a
  media service or a stream.
- Local logs are private files with bounded rotation. They can contain sensitive
  text; privacy redaction runs before the ZIP is shared. JSON and JSONL are
  traversed structurally. Fragmented helper/Node output is assembled into bounded
  complete lines before persistence; oversized lines are omitted. Anonymous labels
  for opaque generation values remain consistent within one export, using a fresh
  secret key per export, so current/pending generations can be compared without
  retaining their original identifiers or enabling cross-export tracking.
- Binary logs, raw dumps and all media payloads remain excluded. Text redaction is
  best effort: arbitrary unlabelled prose cannot be proven free of private data.
- Each text log retains at most 32 MiB, on a complete line boundary; total text is
  bounded to 128 MiB. New collection work stops after 45 seconds, commands time out
  after five seconds, and the parent bounds the worker lifetime. Missing evidence
  remains explicit. These budgets prevent an export from hanging indefinitely.

## Validation and staged app

The Python regressions construct activation/client/runtime/application state, a
read-only installer DB with unrelated private records, symlinked published native
modules, mod logs and simulated service responses. They verify archive contents,
coverage references, redaction, exclusions, partial results and size/deadline
limits. Logger tests verify rotation, permissions, exact relay output and oversized
line handling. Node regressions verify fragmented output, trusted renderer/error
capture, normal stream semantics, helper lifecycle and existing updater/mod APIs.
The shell launcher regression runs its real diagnostic relay and preserves the
original launch protocol.

The real Electron 46 settings harness passed with a fresh signed-out profile and
persistent diagnostics installed, including a synthetic renderer warning and real
ZIP worker. Evidence: `out/diagnostic-audit-verified/`. The exporter and all
its diagnostic dependencies were staged into `out/dev/versions/current/`; all five
export regressions also run against that staged handler/worker. Existing dev
launcher and updater transaction files were preserved. New early-launch/Python
instrumentation ships through normal launcher/application rebuilds. JavaScript
recording starts when the updated entry/helpers load on the next ordinary app
restart; already-running code and historical sessions cannot gain past records.

No login, message, call, physical media acquisition or privilege prompt was used.
Synthetic checks establish collection/recording behavior, not real-call quality.
