# Managed capture build — 7 November 2026

`capture-latest` now runs Native16 with [silent verified KMS maintenance](kms-silent-maintenance-20261107.md).
The root-owned maintenance authority is installed. The corrected refresh succeeds
without authentication and automatically binds a fresh managed activation.
Fresh launch/relaunch, silent grant rotation back to dev, direct/hosted thumbnails,
and a live KMS call pass. Older DPE/Node maintenance requests leave the grant
unchanged, and the older DPE capture caller receives no pixels. Lint, 787 Python
tests, 462 JavaScript passes (three skips) and 92 Rust passes qualify this version.
The helper denies direct execution (mode 0744). Runtime, native bridge and encoder
identities are retained; the capture manifest is
`46e657244ab33a03bbbd8b4fce19b5257cf28073058a3f580c75eeb5874d4ffe`.
The KMS server is `d3064bdc74b5956a0ae7597143f0282a04f2f7470813c7996824368956a08b99`;
the verifier is `790ab93827e2b31a86e177667f8ffa91e98423c84c9798b2c6d32637a6b0da39`.

## Native14 checkpoint

At the previous checkpoint, `capture-latest` contained Native14, including automatic KMS helper maintenance
and executable-bound grants. The dev profile runs this build. The clock profile
remains on Native13 (`capture-rust-clock6`); it is not granted the new executable's
KMS access. The retained `capture-rust-native14` candidate uses the same native
binaries. See [KMS updater behavior and checks](kms-updater-binding-20261107.md).

Native14 retains the runtime and encoder/bridge hashes below. Its new identities are:

- KMS server: `740df4cd5b57ac89fb3410e3eeeef41a9448f4d1b11cf3da631913a0b328acaf`.
- Direct producer: `a9b3af6b4fb7014eb68d24b48e720cdf434cc23b0828ea7b97d53ebf60c36737`.
- Latest capture manifest: `8c119b6faada320d5fa97fb6ff839496e3d5bdc182dad948895d8e47555c0881`.

The automatic migration opened desktop authentication on activation. Its first
attempt timed out; the manual retry completed and installed the Native14 server
with the exact hash above. The version-3 root-owned grant matches the active dev
executable. The older clock executable and an unrelated Node caller are rejected
without pixels. Live KMS sharing, changing previews, and an explicit systemd-hosted
thumbnail pass; both accounts are disconnected and owned capture helpers exit.
Lint, 781 Python tests, 459 JavaScript passes (three skips), 92 Rust passes and
the real screensharing settings harness pass. The previous latest tree is retained
as `capture-latest-native13-before-native14`; Native13 remains independently usable
for paths not requiring the newly scoped KMS grant.

## Native13 capture qualification

`capture-rust-native13` retains the Native13 checkpoint and uses `config/clients.lock.json`
(Canary 1.0.2357). Start it with:

```sh
bun run dev run capture-latest --profile dev --watch
```

At that checkpoint the dev profile ran `capture-latest`; the independent clock profile runs
`capture-rust-clock6`. Both native payloads are byte-identical to the retained
`capture-rust-native13` checkpoint. Native changes require a new build; watch
mode refreshes supported scripts only.

All DPE native helper implementations now build from Rust, including camera,
preview, picker, KMS/Wayland/game sources, native bridge, synchronization and
encoder policy. Pinned upstream libraries, Electron patches and C test callers
remain separate. See the [migration and profiling evidence](capture-rust-profiling-20261106.md).

Electron comes from verified artifact `out/dev-runtime-20261106/`, compiler run
`41282631878`, executable SHA-256
`6d0e3db61278f7be23afbb0129361d6fe4d30c9547e7fe5d1248740b9af57cb5`.
The later presentation-clock, AV1 crop metadata and media-content-only Electron
ports in the working lock are absent from this retained runtime. The known
1920×1080 AV1 case can still decode as 1920×1082.

Native13 identities:

- Native helper: `012f09869ef1b5de53499cde14e640109af9760eb16f8ad6928f6fa1c0f44828`.
- Private Rust encoder: `27e5d873efd50fe79123b368661c803d8deab786459ca578f9d1a63bb7451087`.
- Direct producer: `372b29f41e5891ca7a653b3e9debab67dcaefcfc34c1cb599a4fe5e5a255ce7f`.
- Vulkan hook: `0722c6759d880a77d2ed51de5e5f155381a32ef15e67850f1a78e7bb30935583`.
- Complete 526-file capture manifest: `8017087a59f3e380df2f6e8bf83c2b3b78faf0eb921fa065a22a4ab273bb6cb8`.

Fresh managed launch and relaunch pass with profile `capture-rust-verify13`;
reports are in `out/live-capture-private/native13-desktop/`. Native AV1 and HEVC
720p delivery and H.264 game lifecycle checks pass with the Rust encoder guard.
Repository lint, 779 Python, 453 JavaScript (three skips) and 88 Rust tests pass.
Native13 repairs SDR FP16 Vulkan swapchains with checked GPU conversion into
the existing export image. Packaged fullscreen Vulkan and GLX live tests deliver
changing receiver frames and advancing previews without 2011; reverse KMS also
passes. Both accounts are disconnected. The nine matched resource runs,
240-FPS contention cases and 60-FPS contention cases use Native12. Runtime,
bridge, encoder, raw producer, KMS/preview sources, GLX hook and game consumers
are byte-identical in Native13; the Vulkan repair is qualified separately.
See the [final comparison and limits](capture-native12-comparison-20261107.md).

At Native13 qualification, the installed privileged helper was the authenticated Rust
Native5 server, protocol 6, SHA-256
`dbb501829a48e316fef2ec78553fa41c10af7d7f549534cd23e920115d84f39a`.
No additional privileged installation was required. The previous latest tree
is preserved as `capture-latest-native12-before-native13`; the independently
usable Native12 checkpoint remains available.

A managed Native14 relaunch after migration retains the exact executable grant and opens
no new installer/authentication process. A fresh systemd-hosted KMS thumbnail
also succeeds after relaunch, confirming that the grant follows the executable
rather than the original Discord PID.
