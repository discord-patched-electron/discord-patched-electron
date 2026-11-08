# Local Electron alpha.6 build, 7 November 2026

The user authorized a local build with unrestricted host resources instead of
Blacksmith. The input is Electron 46.0.0-alpha.6 at
`c76ee1bdf7fcbe45c16344c607852164c82e2b7b`, taken from the immutable `main`
snapshot `2379ebd`. This is a diagnostic build; runtime qualification remains
pending.

The final dev lock also retains `prefilled-spellcheck.patch`,
`presentation-clock.patch`, `av1-crop-metadata.patch` and
`media-content-only.patch`. All four apply cleanly on alpha.6. The initial local
compile uses the main-derived baseline, which omits those patches. Let that
compiler finish, then migrate to the verified complete dev inputs at the same
physical path and reuse its objects before treating the requested build as
complete. The final assembly report is `out/local-alpha6-dev-port-source.json`.

The initial compile reached 39,943 tasks before the video-sink object failed on
the removed `cross_thread_copier_base.h` include. The sink uses the retained
`cross_thread_functional.h` API and thread-safe refcounted receiver. The additive
`alpha6-compile-context.patch` removes the obsolete include; the exact failed
object then compiles (`out/alpha6-video-object.log`). Fresh final assembly passes
in `out/local-alpha6-header-verified.json`. With the compiler stopped, all four
dev patches were applied to the retained Electron tree and checked against that
fresh tree before replaying hooks (`out/local-alpha6-final-hooks.log`).

## Dependency port

The first hooks attempt failed before compilation. The additive, checksum-pinned
`patches/ports/46/alpha6-dependencies.patch` preserves the imported patch files
and refreshes these three dependency patches:

| Patch                                                                    | Disposition                                                                                                                                                                        |
| ------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `direct_video_sinks.patch`                                               | Retain DiscordVideo interfaces, bindings and context feature. Refresh list context around upstream device-bound sessions and retain upstream unbounded-element privilege state.    |
| `av_add_ability_to_get_native_gpu-backed_textures_out_of_chromium.patch` | Retain the frame-owner capture-target API beside upstream's new `FromWindow` declaration. Preserve all other capture/audio authorization and lifetime changes.                     |
| `fix_pointer_lock_in_views_that_only_receive_forwarded_input.patch`      | Reuse the existing screen include. Sample legacy cursor positions and acquire capture while preserving upstream's feature-gated screen-position restoration and headless behavior. |

Other changed patch lines update full blob identities or hunk offsets; they add
no behavior. Fresh assembly reconstructs the pinned alpha.6 tree. The complete
dependency hook sequence passes, and reverse application checks confirm that
all three refreshed patches match the resulting Chromium checkout.

## Workspace and evidence

The retained container uses 20 Ninja jobs with CPU quota `max`. Its RAM and swap
limits exceed host capacity. No Blacksmith job was dispatched. Dependency
checkouts were reset only through the build driver's recorded post-sync bases;
no healthy compiler was restarted.

The input migration retains the original physical checkout path. It records old
and new inputs in `/work/local-latest-alpha6/workspace-migration.json`, verifies
the fresh assembly tree against the cached Electron index, and preserves all
downloaded dependencies. No compiler objects were moved or deleted. The local
resume wrapper maps the new identity to this verified workspace.

Host evidence is under `out/`: `local-alpha6-port-source.json`,
`local-alpha6-hooks-validation.log`, `local-electron-alpha6-resumed.log`,
`alpha6-port-lint.log` and `alpha6-port-tests.log`.

Discovery now counts an explicitly recorded local compiler attempt toward its
96-hour cooldown. The attempt is recorded in `ci-state` without a Blacksmith
reservation or charge. Security bypass rules remain active. The complete
upstream alpha.1-to-alpha.6 behavior review, completed compilation and runtime
qualification are separate outstanding gates; passing hooks proves patch
application, not runtime compatibility.

The final hook replay also required refreshing the media-session declaration
hunk in `dpe_media_content_only.patch`: alpha.6 has a UMA reporting comment
where the old hunk expected a second blank line. The compile-context layer
updates only that surrounding context and retains the added declaration and
implementation. Fresh complete assembly is recorded in
`out/local-alpha6-complete-verified.json`; exact hook replay is recorded in
`out/local-alpha6-complete-hooks.log`.

The complete-input compile later reached 45,225 tasks before the Discord source
and decoder hit the same removed copier headers. A source audit found three
remaining includes across those two files and no direct `CrossThreadCopier`
uses. `alpha6-electron-includes.patch` removes those includes while retaining
the cross-thread functional API. Both exact objects compile successfully
(`out/alpha6-electron-video-objects.log`), and fresh assembly reproduces the new
tree (`out/local-alpha6-includes-verified.json`). The recorded migration changes
only those two Electron C++ files and preserves the completed dependency hooks
and objects. Distribution build output is `out/local-electron-alpha6-includes.log`.

## Completed artifact

The complete dev-input build finished on 8 November. Its archive is
`/mnt/storage/discord-patched-electron-ci/local-latest-alpha6/runtime-dist.zip`
(122,801,866 bytes), SHA-256
`675ab047f31ed82fa72c87b0198fbc824bab76fb02a388bedd618ec7438d812f`.
The native key is
`9ce794464fc2288eb95783be29deb2c7fc5d0c57c2d71aa48a68bc56669d79eb`.
Independent hash, ZIP CRC and native-key checks pass. The extracted executable
reports Electron 46.0.0-alpha.6, Chromium 159.0.8130.0 and Node 26.13.0 in Node
mode. Evidence: `out/local-alpha6-runtime-verification.json`.

The local attempt is recorded as compiled in `ci-state`, preserving its original
cooldown timestamp and zero Blacksmith charge. This does not replace the
last-good runtime or publish/approve a release. Desktop/native-media runtime
qualification remains outstanding.
