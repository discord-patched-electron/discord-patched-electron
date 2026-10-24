# Update ownership and scheduling

The package owns the local Discord host ASAR, `discord_desktop_core/core.asar`
and native module files. Their built-in host/module updater is disabled so it
cannot replace the patches or mix module revisions. Discord's HTTPS web UI still
updates independently. Installed Arch packages update through pacman/AUR helpers;
a CI release does not silently modify a running installation.

## Independent update paths

`update.yml` polls at minutes 7, 22, 37 and 52 every hour (GitHub schedules can be
late). It compares all three official distribution manifests, including every
module revision and SHA-256. A module-only update counts even if the host version
is unchanged. Healthy channels continue when another manifest fails. An Electron
API outage also leaves Discord discovery operational.

Each changed client/integration identity immediately qualifies on the pinned
last-good compiled runtime through `client-update.yml`: bundled baseline,
custom native video and offline Krisp, Wayland/X11 application audio, packaging,
and actual login-form launch/relaunch with and without optional Equicord. These
jobs never compile Electron. Each job consumes the same immutable `ci-state`
commit; the three channels run independently. A failing gate keeps the previous
qualified installation usable and retains diagnostics.

When a **Discord host archive** changes, `inspect_bundled_runtime.py` reads the
checksum-verified archive without executing it. It hashes the Electron executable
and the native runtime payload separately from ASARs. It also records the embedded
Electron version when unambiguous. It does **not** poll Discord fork commits.
The source fork is consulted only when a shipped binary change or failed native
compatibility test calls for a port review. A changed executable alone does not
force a rebuild: branding/build changes can leave the existing bridge compatible.

Example verified on 24 October: Canary 1.0.2357 -> 1.0.2365 changed client files,
but both the bundled executable and native runtime payload remained identical,
reporting Electron 44.9.3. The custom runtime remains Electron 46 alpha 1.

Upstream Electron alpha/beta releases are retained in the audit ledger, but older
pending prereleases are coalesced into the newest. Routine source advancement is
eligible **96 hours after the last compiler attempt** (initially the pinned good
build's completion). No new eligible input means no compilation. Official Electron
high/critical security advisories bypass the wait only when their comparator range
includes our current version and explicitly identifies a fixed candidate on that
major. Unknown ranges and cross-major backport assumptions are not guessed.
`urgent_versions` in `config/update-policy.json` provides an explicit maintainer
exception for embargoed fixes/Chromium advisories that lack machine-readable data.
This is not a guarantee that every urgent Chromium fix will be labeled publicly.

`advance_electron.py` first applies the existing patch queue in a disposable source
checkout. An incompatible patch fails visibly without touching the retained PC
build or the last-good runtime. A successful application advances only the source
lock by a fast-forward main push and explicitly dispatches `build.yml`, with an
expected native-input key. The compiler checks that identity before starting.
Only the self-hosted PC is used; no Blacksmith. A healthy compiler is never
cancelled or duplicated. Reviewed major-specific ports remain necessary when
upstream APIs change: avoiding merges does not remove compatibility work.

## Durable state and artifacts

`ci-state` contains generated JSON only and is never merged into main. Content
keys reserve client and Electron work before jobs start; completed/failed attempts
remain in the ledger. Repeated polls do not restart failed identical work. Use
Actions **Re-run failed jobs** after resolving an infrastructure issue; a source
or client fix generates a new integration key. State writers share one concurrency
group and use normal fast-forward pushes, never forced JSON merges.

The reviewed last-good runtime is pinned by compiler run, source commit, complete
capacity-report digest and archive checksum in `config/update-policy.json`. It is
retained in a public **runtime-only prerelease** so Actions artifact expiry
cannot force a recompile. Newly compiled Actions artifacts/reports retain 90 days.
Fetch verifies both the report and archive hashes. A newer source lock does not
invalidate the exact older pinned runtime used by immediate client checks.

Only source/state advancement jobs get contents-write permission. Compilation and
client tests do not need a Blacksmith key. Native build fingerprints exclude ASAR,
launcher, Equicord and application-audio sidecars, so their updates reuse C++.

## Qualification versus publication

This automation now discovers, reserves, checks client updates, preflights new
Electron sources and schedules PC compilation. **Client/AUR publication
and automatic promotion of a newly compiled candidate to last-good remain release
work.** There is no AUR publishing key configured. A passing source application or
bundled baseline is not permission to publish an unqualified candidate. The
current local app can be rebuilt immediately from the discovered client snapshot
with `build_local_app.py --clients <snapshot>` and the retained tested runtime.

Before enabling AUR publication, the release coordinator must promote only after
native/audio/real-launch gates, retain immutable assets, increment `pkgrel` for
ASAR/module-only changes, push PKGBUILD and `.SRCINFO` together, and leave a durable
retryable publication record if an AUR push fails. It must never hold client
updates behind the 96-hour Electron cadence. These remaining gates are deliberate
and visible; hourly discovery alone is not described as a complete auto-updater.

## Earlier evidence and manual checks

The first two CI runs succeeded. The second reported no input change and left
the remote `ci-state` commit exactly unchanged, with no compiler job allocated.
The activation baseline is **2026-10-23 19:59:51 UTC**. Both runs discovered
Stable 1.0.166, PTB 1.0.224 and Canary 1.0.2357; the new PTB/Canary revisions
subsequently passed the selected-channel checks described below.
See [CI evidence](evidence/discovery-ci.json). The accompanying source, unit,
native-baseline and nine-package checks passed at `c006ba1`.

API references: [release pagination](https://docs.github.com/en/rest/releases/releases#list-releases),
[Git references](https://docs.github.com/en/rest/git/refs#get-a-reference),
[annotated tags](https://docs.github.com/en/rest/git/tags#get-a-tag).


## Validate a discovered client update without compiling

`native-runtime.yml` and `package-check.yml` accept `clients_state`, the immutable
40-character commit of `ci-state`, and `discord_channels`, a comma-separated
selection such as `ptb,canary` (default `all`). Both fetch and retain exactly that
snapshot and its provenance. A branch name is rejected. Omitting the commit uses
the reviewed bootstrap locks. Discovery errors remain in the provenance; passing
a retained older lock does not authorize advancing an affected client channel.

The native workflow accepts an existing successful compiler run through
`reference_run`. It checks the selected clients against that downloaded runtime;
without it, it checks their official bundled runtimes as a baseline. Neither
workflow compiles Electron. Each selected channel receives a separate report,
and a failure does not prevent the remaining channels from being tested. The
job still fails if any selected check fails. A subset pass never implies that
unselected clients passed.

The Arch workflow shares the same packaging implementation as the main Checks
workflow. It builds the prerelease-Electron recipe for each selected client
with an explicitly unusable dummy runtime. This verifies private installation
paths, native library dependencies, preserved Krisp patch bytes, metadata and
sandbox permissions, but cannot qualify a real runtime. Staging trees and dummy
package archives are removed after each attempt, including failures; recipes,
logs and reports are retained.

Local validation of the exact discovered PTB 1.0.224 and Canary 1.0.2357 snapshot
passed all six then-enabled packaging combinations. The current scope has one
prerelease-Electron package per client. Both clients also passed native frame
handoff/teardown and offline Krisp processing using their bundled Electron
44.9.3, with 53.6 dB synthetic noise reduction. These are baseline results, not
custom-runtime, live-call or per-application audio qualification. The existing
narrow dispatch-library exception required no change. See
[local evidence](evidence/client-updates-local.json). The CI snapshot checks subsequently passed;
[CI evidence](evidence/client-updates-ci.json) records both runs. The bootstrap
locks now include those exact PTB/Canary snapshots.
