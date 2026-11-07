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
