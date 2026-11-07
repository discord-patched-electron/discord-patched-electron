# Canary 1.0.2472 compatibility

Evidence: `out/discord-update-20261107/`. This update reuses Electron
46.0.0-alpha.1 from compiler run 41282631878, archive SHA-256
`981f5d9cfcace0b4491807c1a2e9180643ae0d59fe4589e5f513cc890990951c`.
The unrelated local alpha.6 compiler was left running.

## Inputs and complete inventory

The last published qualified candidate is 1.0.2433, release
`v20261103151939.0.0-canary.a25b0596b8ee7d3c`, source proposal
`b17b9617701dadb77c4bddd65339fe61b08d4b80`, discovery state
`823233d5df0a1c53887ace96d23a4c2065c6bd04`. The candidate is 1.0.2472,
discovery state `5b28321845a333d1db9a3a43ed948a9c6f69b300`, checked against
Discord's live manifest. Review started at source
`ea3ac402d21c3dbf372cb32046291a787cd4dd41`.

Both immutable locks and provenance files are retained under `before/` and
`after/`. `inventory.py` downloads and SHA-256 verifies all 15 archives per
snapshot, inventories path/type/mode/link/content in both directions and extracts
both ASARs. All 2,966 `New` file hashes in the distribution manifests also
match their extracted files (`manifest-validation.json`). `inventories.json`, `changes.json` and `dispositions.json` account for
all 57 changed entries. `diffs/` retains complete original/formatted comparisons;
`native-inventory.json` records ELF sections and exported ABI changes.

| Changed payload / hunks                                                                                      | Disposition and affected integration                                                                                                                                                                                                                       |
| ------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Stock core runtime, PAKs, snapshots, updater addon                                                           | Replaced by retained Electron and DPE's loader; stock runtime changes do not require rebuilding the custom runtime for a client update.                                                                                                                    |
| Stock bootstrap bundle, package/build metadata, splash preload/CSS and removed dice image                    | Legacy updater/manual Linux download UI removed upstream. DPE supplies its own bootstrap/splash/updater; preserve upstream originals only as review evidence.                                                                                              |
| Desktop core bundle: constants, module/version/install IPC, injection, menu/tray/timer/check/restart/history | Same legacy-to-native updater migration reviewed for [1.0.2444](client-2444-compatibility.md). Existing DPE native updater supplies these contracts and rejects unavailable modules. Retain failure/offline/concurrency policy.                            |
| Desktop core bundle: shared-texture overlays and screen listener cleanup                                     | Preserve upstream offscreen positioning, suppressed show, transparency and cleanup. DPE excludes overlays from ordinary popout adaptation.                                                                                                                 |
| Main preload: module cache listener, updater flag, removed bootstrap event                                   | Existing adapter supports `hasUpdater`; running-generation module versions remain stable until restart. No removed legacy event consumer needs reinstatement.                                                                                              |
| Frame preload: removed event enum; all three Sentry IDs                                                      | Preserve enum removal; debug IDs are opaque. Blocked telemetry remains blocked.                                                                                                                                                                            |
| RPC/voice/spellcheck manifests and cld Makefile                                                              | File sets unchanged; ordering only.                                                                                                                                                                                                                        |
| Erlpack, game-utils, NDI addon                                                                               | Executable/data sections unchanged; build/debug identity only.                                                                                                                                                                                             |
| Cloudsync, sysimg, zstd, modules, Krisp                                                                      | Opaque release/build data changes; executable sections and exports unchanged. Krisp structural patch and offline API exercised.                                                                                                                            |
| Dispatch, utils, NDI executable, GPU encoder helper                                                          | Preserve native implementation changes and unchanged public exports. No DPE instruction patch targets these payloads. Dispatch retains OpenSSL 1.1 dependencies; its packaging exception is confined to the exact reviewed binary and those two libraries. |
| Voice binary                                                                                                 | Port the complete ordered patch chain and independently update Python scopes and compiled picker storage/wake guards. Preserve added audio-buffer/spatial-audio exports and unrelated upstream implementation.                                             |

## Native port

Original voice SHA-256:
`d069bc035b4dab698223a45829e52a096680c76fb8adf0068108acc0a5f8b56e`.
Prepared SHA-256:
`3956c8e932bea891ecdf4621fcd4c986bb461460edf875717c934797a021a2a7`.
`scripts/native_client_2472.py` records every intermediate identity and edit.

`port-review.log` and `abi-review-final.log` compare with the independently
reviewed 1.0.2444 voice binary. HRD encoder bodies, pool dimensions, decoder,
presentation clock, picker C entry points, delivery, selected capture wake
hosts/targets, scaling, synchronization and dav1d wrappers retain normalized
instruction bodies. Decoder calls resolve to corresponding symbols and string
operands retain their contents. Replacement relative operands were relocated
against these targets. Shader bytes match the complete reviewed original asset;
the pacing default remains unique. Selection storage moved to `0x231fb90`;
compiled picker and publication-wake tables now agree with the Python scope.

New metadata travels through package sources, updater staging, compatibility
keys, fingerprints and CI selection. Unknown whole-file hashes and altered
instructions still fail closed. The executable decoder regression includes the
new replacement, send errors, receive EAGAIN and cleanup. Repreparing the final
module leaves its hash unchanged (`reprepare.json`).

## Validation and limits

The managed version is `discord-latest`:

```sh
bun run dev run discord-latest --profile dev --watch
```

`dev-create-final.log` records assembly from the retained artifact and current
working-tree adapters. Temporary automated profiles are removed after use. To recover space after the
filesystem filled, bulky downloaded/extracted comparison files moved to
`/tmp/dpe-discord-update-20261107-evidence/`, with links from the evidence root;
small inventories, hashes, reports and logs remain under `out/`.
No account login, messages, calls, physical camera recording or screenshots were
used. This is local dev validation, not public release approval.

- `managed-login-prepared/` and `managed-relaunch-prepared/` pass Welcome
  completion, login, all module-version APIs, `hasUpdater=true`, four normal
  popout open/close handshakes per launch and clean shutdown through the manager.
  Initial managed launch exceeded the harness deadline during module preparation;
  `dev-prepare-final.log` records completion before these launch checks.
- `screenshare-pipewire-full/` passes capture, picker cancellation, scaling,
  synchronization, VP8 encode/decode, frame delivery and framing.
- `screenshare-h264-guard/` and `screenshare-av1-guard/` pass GPU encoding,
  actual decoding, production encoder/capture hooks, picker cancellation and
  framing. H.264 uses the verified OpenH264 library; AV1 includes software crop.
  `screenshare-hevc-guard/` also passes HEVC with the native Wayland/Electron
  receiver on the explicitly selected GPU.
  The earlier AV1 run without the encoder adapter failed framing and is retained.
- `native-basic/` passes native frame delivery, ownership/teardown, native voice
  loading and offline processing. `krisp.json` verifies 53.6 dB noise reduction.
- `native/` retains a failed scheduled-burst presentation check: the retained
  runtime presents 15 callbacks for 45 submissions. Basic delivery passing does
  not establish scheduled presentation correctness; the separate alpha.6 build
  remains outside this task.
- `mods-qualified/mod-report.json` passes official pinned Vencord and Equicord
  installation, executable self-update, relocated package-update preservation,
  relaunch and uninstall. This scoped run reuses a freshly prepared,
  unauthenticated fixture identity and skips duplicate popout checks; those
  pass separately on both managed launches. Earlier mod attempts retain a
  popout-handshake timeout, disk-full copying and an incorrect nested fixture
  profile as failed evidence. No production checks were relaxed.
- `bun run test` passes: 802 Python tests, 468 JavaScript passes and three skips,
  plus the Rust workspace tests. `bun run lint` passes. Initial checks failed
  when the filesystem filled; the successful reruns follow removal of redundant
  task-created copies and relocation of bulky evidence to temporary storage.
- Initial launch preparation correctly rejected the unknown binary. Early fixture
  attempts also retain input/setup errors (unfinished dev assembly, omitted
  companion libraries, and unsupported detailed content without a GPU producer).
  These are preserved rather than overwritten as passes.
