# Bug report ZIP export — 5 November 2026

See the [subsystem coverage audit](diagnostic-coverage-20261105.md) for the expanded
collector, persistent logging, state inventory and current validation.

Troubleshooting provides **Logs for bug reports → Export ZIP**. Restricted settings
IPC opens the native save dialog and runs a standard-library Python collector
outside Electron's main thread. The status shows the saved destination; export
errors appear beside the button.

GPU information has a 1.5-second deadline. Optional diagnostic failures cannot
block the archive, and systemd commands have five-second deadlines. Collection
retains text logs from the active profile, versioned hosts, active native modules
and updater. Each log keeps its latest 32 MiB, starting at a complete line. Gzip
logs are decompressed within the same limit before redaction. Total text input
is bounded to 128 MiB; collection stops taking new files or diagnostics after
45 seconds. Tree scans and the worker runtime are also bounded.

The exporter first tries a private temporary directory beside the destination and
publishes the completed ZIP by rename. File-only portal grants fall back to a
private system temporary directory and copying into the selected file. A failed
collection leaves an existing destination untouched. Cancellation, failure and
success release the export lock and clean up temporary files.

## Privacy

Text, structured JSON, metadata, archive names and manifest errors pass through
the shared redactor. It suppresses credentials, tokens, authorization/cookie
headers, private JSON fields, emails, phone numbers, IP/MAC addresses, Discord
IDs/tags, URLs, local identities and user paths. ANSI escapes are stripped before
matching. Account stores, settings, caches, binary logs and undecodable text are
excluded. Arbitrary unlabelled personal text cannot be reliably identified, so
redaction is explicitly best effort and the UI asks users to review before sharing.

Raw minidumps and ELF core files are excluded because their memory can contain
credentials, conversations and images. Up to five newest local crashes contribute
summaries containing size, time, format and an exception code where available.
Systemd contributes allowlisted metadata and redacted stack frames for up to three
recent crashes. Queries cover seven days and match the current user and Electron
executable. Environment variables and command lines from crash metadata are
excluded. The manifest records unavailable evidence. No upload occurs.

## Validation

- Python archive regressions check logs, crash selection, size limits, compressed
  input, binary/invalid-text exclusion, missing profiles, redaction across every
  archive entry and scoped systemd stack/journal collection without raw dumps.
- Node regressions use the real Python worker to reproduce a GPU query that never
  finishes, file-only destination permissions, failed writes, missing profiles,
  worker startup failure, cleanup, retry and preservation of existing output.
- The real Electron 46 settings harness uses a fresh signed-out profile and a
  retained runtime. Its `troubleshooting` scope covers the button, preload, save
  handler and worker, cancellation, concurrent requests, inline errors, destination
  feedback, private ZIP permissions, focus and narrow layout at 150% zoom.
  Evidence: `out/export-logs-redaction-final/`.
- The full settings harness previously stopped before Troubleshooting on an
  unrelated existing color assertion: `rgba(151, 151, 159, 0.12)` versus `0.16`.
  Evidence: `out/export-logs-settings-check-2/settings.json`.
- No account login, call or physical capture was used. System crash responses and
  portal filesystem permissions use fixtures; no real crash was induced and no
  real portal permission grant was automated.

## Managed staging correction

The existing dev watcher copied `export-logs.cjs` but excluded all updater Python.
The running handler consequently tried to launch a missing `export_logs.py`.
The source settings harness did not catch this because it loaded repository files.

`dev.editable()` now explicitly allows only `export_logs.py` and `log_redaction.py`
among updater Python. Other updater code remains frozen. A sync regression starts
with a payload missing both files, stages them and runs the staged worker to
verify a redacted ZIP. Node export regressions also support
`DPE_TEST_EXPORT_HELPER_ROOT` to load the managed app's handler and worker instead
of repository files.

Both missing workers were staged into `out/dev/versions/current/` using atomic
`copy_changed`, without restarting Discord or changing its profile. All five Node
export regressions pass against that staged directory. The next button click can
use the workers immediately. Restart an older dev manager to load the new sync
rules before subsequent Python helper edits.
