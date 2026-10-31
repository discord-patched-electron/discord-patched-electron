# Installer resilience and GitHub API limits

The read-only host diagnostic at 22:12:29 UTC on 31 October 2026 reported
60/60 anonymous core API requests used, with zero remaining. This confirms quota
exhaustion but does not identify which applications consumed every request.
[GitHub documents](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)
that this quota is shared by originating IP. Authenticated conditional requests
can avoid consuming quota on 304 responses; unauthenticated ETag requests are
not promised that exemption in the
[best-practices guide](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api).

## DPE update discovery

Application, client and Electron release discovery previously queried REST
`releases/latest` before reading the existing manual approval record. Each layer
has a cooldown, but multiple profiles and installations have separate caches.
Discovery now reads the approval record first and fetches only its fixed signed
release assets. No REST request or personal token is required. Actual live
discovery and GPG verification succeeded while the anonymous quota was exhausted.

Signatures, manifest hashes, channel/track checks, version ordering and manual
approval remain required. The marked `manual-approval` compatibility descriptor
retains the old `github.json` shape for installed Bootstrap API 1 readers; it is
not presented as a GitHub API response. Tests stage a generated proof through the
unchanged installed loader. Older API-era offline proof remains verifiable.

## Official installer findings

The pinned binaries remain Vencord Installer 1.4.2 and Equilotl 2.3.0, with their
existing SHA-256 checks. The reviewed sources are:

- [Vencord downloader](https://github.com/Vencord/Installer/blob/v1.4.2/github_downloader.go)
  and [installer self-update check](https://github.com/Vencord/Installer/blob/v1.4.2/self_updater.go).
- [Equilotl downloader](https://github.com/Equicord/Equilotl/blob/v2.3.0/github_downloader.go)
  and [installer self-update check](https://github.com/Equicord/Equilotl/blob/v2.3.0/self_updater.go).

Both perform a payload-release lookup and start an installer self-update lookup.
Both contain their own official fallback for blocked/rate-limited release
metadata. The development-install switch suppresses payload acquisition, not the
self-update check, so setting it is not a reliable API-free installer mode.
Their mod self-updaters also retain their upstream network behaviour.

The previous private bootstrap staging did not isolate payload writes. Inherited
mod-directory variables could cause the CLI to overwrite files outside that
staging area before loader validation. The revised helper forces only its
installer subprocess to use a new durable payload generation. Runtime settings
paths remain unchanged. It verifies the expected wrapper target, required files,
archive bounds, absence of symlinks and the original bootstrap backup before the
atomic loader replacement. Failed generations cannot replace the active payload.

Recently verified payload generations can be reused for 30 minutes. Reuse checks
all recorded file hashes and the pinned installer identity. Changes made by a
mod's self-updater invalidate that cache instead of being rolled back. Repeated
same-mod installation remains a no-op. These paths avoid launching a CLI and its
two API checks unnecessarily; a new CLI run still has upstream API requests.
Verified cache reuse also works offline. A missing, expired or changed cache
requires an online install and does not bypass verification.

## Downloads and failure handling

Shared downloads use a temporary sibling, bounded reads, length validation,
fsync and atomic replacement. Transport interruptions and temporary HTTP errors
get one retry. Size violations, permanent HTTP errors and verification failures
do not trigger that retry. Recognized rate limits and Retry-After pauses are
remembered per download host within the profile. The startup error screen stops
its ten-second automatic retry loop for throttling. The standalone first-install
bootstrap also stops immediate retries on 429 or a server Retry-After response.

The installer remains bounded by a two-minute timeout and uses classified errors
for storage, permissions, incomplete downloads and rate limits. Failed rate-limit
responses without an exposed reset time impose a one-minute CLI cooldown. No
raw installer output is shown as UI diagnostics. Only its final 16 KiB is loaded
into memory for error classification.

Evidence is retained under `out/validation-storage/mod-network-resilience/` and
`out/validation-storage/update-api-live-proof/`. Tests use disposable profiles,
fault injection and signed fixture releases. No account login, message, call or
physical camera capture is part of this work.

Final validation passed: 457 Python tests, 189 JavaScript tests, lint and 27
settings/UI check groups. Both actual pinned CLIs installed into a disposable
profile while the REST quota was exhausted, then reused their verified cache for
remove/reinstall. A separate real-client matrix loaded Vencord `8d64c8b`, switched
to Equicord `00abcbe` and uninstalled, reaching Discord's login form after each
step. Reports are `cli-smoke/report.json`, `desktop/mod-network-report.json` and
`settings/settings.json` under the evidence directory above.

The current working-tree app is `out/validation-storage/mod-network-reviewed/`;
`out/launch-test.sh` selects it. Work remains uncommitted.
