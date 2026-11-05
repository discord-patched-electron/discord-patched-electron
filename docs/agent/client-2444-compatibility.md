# Canary 1.0.2444 compatibility

Comparison: the last published candidate, 1.0.2433, against the exact 1.0.2444
client lock from discovery run 41936340989. Downloaded proprietary files and
comparison output stay under `out/client-2444-compat/` and
`out/discovery-csp-fix/`; none are committed.

## Desktop and preload diff disposition

- Discord removed its legacy module updater, bootstrap injection, event bridge,
  stock-host migration handler and legacy update constants. DPE now supplies the
  remaining native-updater interface from its existing module updater. It never
  instantiates Discord's native updater or downloads a stock host.
- Menu, tray and periodic checks use desktop core's native notification flow.
  Concurrent requests share DPE's preparation task; completion is advertised
  only after successful preparation. Errors reach Discord's existing error flow.
  Restart still uses the package launcher. Background work cannot replace the
  modules or native paths of the running generation.
- Native module-version queries and committed-module paths come from the running
  snapshot. Module install requests accept only modules already present in that
  approved snapshot. Missing modules reject instead of succeeding without an
  installation. Native build metadata has no upstream manifest number and uses
  zero, without inventing a Discord build number.
- The preload renamed `canBootstrapNewUpdater` to `hasUpdater` and removed its
  legacy module-installed listener. DPE does not consume the removed property;
  the supplied updater makes the new property true. Cached module versions stay
  correct because a generation changes on restart. Launch qualification now
  verifies the exposed updater flag and every module version against the lock.
- The removed `UPDATE_ENDPOINT` and old event constants have no remaining DPE
  consumers. OpenAsar can still pass its older bootstrap fields; desktop core
  ignores them. Its legacy startup updater continues to use the same DPE API.
- Shared-texture overlays now suppress show/showInactive, stay transparent,
  reposition outside all displays on monitor changes, and remove their screen
  listeners on close. Preserve that upstream behavior. DPE's popout adapters
  already exclude out-of-process overlays; normal video popouts are tested
  through the actual updated core. The removed macOS Squirrel setting has no
  Linux behavior to port.
- The Sentry protocol fallback is present in both compared preload versions.
  The reported CSP error is blocked telemetry, separate from updater failure.
  No CSP relaxation or telemetry allowance was added.

## Complete payload inventory and final review

The final review also downloaded and checksum-verified every source in both
immutable client locks, including the stock host. Every archive member and ASAR
member was compared by path/type/mode/link/content hash. The complete union has
39 changed entries; all 39 have explicit dispositions in
`out/client-2444-compat/full-inventory/dispositions.json`. Supporting inventories,
ELF section/export/dependency comparisons and complete text diffs are adjacent.
Every `New` file hash in each distribution delta manifest matches the inventory.

- Stock `DiscordCanary`, `libffmpeg.so` and `updater.node` changed, as did the
  stock bootstrap/splash. Bootstrap changes remove legacy updater machinery and
  unused yauzl code; splash changes remove its manual Linux download UI and
  assets. DPE replaces these payloads with its own loader, pinned OpenAsar,
  splash, updater and verified runtime. These removals require no replacement
  code in DPE. Build-info changes are the expected host version and Sentry
  release identity.
- Erlpack, game-utils, NDI and utils change only debug information/build IDs;
  executable/data sections, exports and dependencies are identical. Their JS
  wrappers and assets are unchanged. Cloudsync, sysimg and zstd are unchanged.
- The modules addon changes its embedded release-commit prefix plus debug/build
  identities. Krisp changes an opaque rodata fingerprint plus debug/build
  identities, with unchanged executable sections/exports/dependencies; its
  structural patch and actual offline test pass.
- Dispatch has native implementation changes but no changed exports or library
  dependencies and no DPE patch/hook. Its pre-existing OpenSSL 1.1 dependency
  limitation remains: no new missing-library exception or unsupported-function
  claim was added. Login/mod checks do not establish functioning optional dispatch
  APIs on a machine lacking those libraries.
- RPC, voice and spellcheck file manifests change only list order. Spellcheck's
  generated cld Makefile changes only prerequisite order; its runtime payload is
  unchanged. All distribution delta-manifest differences match the pinned files.
- The changed desktop core, both preloads and voice binary are covered by the
  compatibility changes and validation described in this note. No archive entry
  was dropped solely because it was binary or not implicated in the first crash.

Future updates must follow the [complete review workflow](upstream-update-review.md)
now required by `AGENTS.md` and linked from discovery/release instructions.

## Native module port

The new original voice module is SHA-256
`d6f5ba68568ad2f8701baf1db699ba052c8c9ce5e09ef8e84b69e4edf0902506`.
Its existing structural PulseAudio review passes. `native_client_2444.py` records
complete input/output hashes and relocated sites for HRD, visible dimensions,
receive-frame EAGAIN handling, chroma filtering, presentation clock and pacing.
The final module is
`09c1ddd4eeb5b1583487b1806a21bf57d90bbf5e56be2dca077d8ed8f0b6ae68`.

The functions touched by instruction patches have identical normalized bodies
to pinned 1.0.2357. Decoder replacements relocate each relative operand against
its corresponding original target; the executable branch regression also tests
the new replacement, including send errors, receive EAGAIN and cleanup paths.
The embedded original shader matches its existing complete shader hash.

Capture C entry points, frame delivery, scale and synchronization functions and
publication-wake hosts/targets were compared by symbol and normalized body.
Relocation-backed scope checks validate the new runtime addresses. Raw function
hashes and picker storage/wake tables are independently updated for this module,
including the compiled helper's second address gate. The five dav1d wrapper
bodies also match after relocation; their raw hashes are added to software-AV1
crop qualification. Scope generators report support for all affected adapters.

Encoder internals are not byte-identical: opaque Rust encoder objects grew.
DPE continues to pass those objects through the exported C interface rather than
copy their private layout. Existing encoder scope checks validate the external
linkage and the specific WebRTC/VA-API hook contexts. Actual codec checks below
exercise the new module with the compiled adapters.

The metadata helper is included in package sources, staged updater scripts,
compatibility keys, integration fingerprints and CI scope selection. An unknown
whole-module hash or changed reviewed instruction still fails preparation.

## Validation

Reports are under `out/client-2444-compat/`. The staged app includes the complete
working-tree integration, a freshly compiled camera/capture/encoder helper and
the retained Electron runtime; it uses a fresh unauthenticated profile.

- `launch/`: login, four normal-video popout open/close attempts and same-profile
  relaunch passed without uncaught exceptions.
- `native-complete/` and `krisp.json`: synthetic native bridge, scheduled frame
  presentation and offline Krisp passed; measured noise reduction was 53.6 dB.
- `screenshare-vp8/`, `screenshare-h264-codec/`, `screenshare-av1/`: capture,
  encoding, decoding, delivery and framing checks passed. H.264 uses the verified
  managed OpenH264 library. AV1 reports 180 submitted/cropped software-decoder
  frames with no rejected or unmatched frames.
- `screenshare-pipewire-current/`: the updated capture picker, selection
  cancellation, scale, synchronization, source delivery and framing checks passed.

- `launch-api/`: both launches also pass the complete module-version comparison
  and report `hasUpdater=true` through the real preload bridge.
- `mods/mod-report.json`: official pinned Vencord and Equicord installers pass
  installation, executable self-update, relocated package update, relaunch and
  uninstall. Their loaders and updated payloads survive without reinstallation.
- `screenshare-hevc-wayland/`: HEVC passes encode/decode, delivery and framing on
  the compatible retained runtime used by the earlier HEVC qualification
  (`c139aa38e2b0f60253d33fc592dd51ec967eca39d222a42c0d39b5dc5e68d1b9`).
  There are 178 GPU submissions, no CPU submissions, import fallbacks or failed
  frames. The earlier Xvfb/software receive attempts remain failed evidence:
  HEVC needs a compatible hardware receiver and no software decoder was added.
- `reprepare.json`: repeating all six patches on the final module leaves its
  complete SHA-256 unchanged and reports each patch already applied.

`bun run lint` and `bun run test` pass (708 Python tests, 430 JavaScript passes
and 3 JavaScript skips). The executable decoder regression covers
all three reviewed revisions; the unit suites cover updater concurrency, errors,
offline mode, approved module requests and periodic/quickstart notification routing.
No account login, messages, calls, physical camera tests or screenshots were used.
These checks do not establish live-call quality or qualify a public release.
