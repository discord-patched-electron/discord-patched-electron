# Implementation status

Updated 23 October 2026. This is a bootstrap implementation, not a working binary release.

## Verified

- The vendored AUR files match commit `3b290dffd5eb840faed445bdc2bdb18572839471`.
- Current manifests lock Stable 1.0.166, PTB 1.0.223 and Canary 1.0.2354 separately.
- The existing Krisp patcher applies to each channel's real native binary. The
  validation wrapper confirms that only the expected six-byte conditional branch
  changes and that repeating the patch is a no-op. Input/output hashes are in
  `evidence/*-krisp.json`; all explicitly say runtime validation is still false.
- Existing AUR resource/bootstrap changes apply to the current Stable app.asar.
- The imported Discord patch exactly reconstructs the fork tree from its base.
- Unit tests cover source integrity, cross-channel isolation, fingerprints,
  byte-preserving failure paths, patch idempotency and Arch prerelease ordering.
- The PC runner is online and has fetched Chromium and installed dependencies.
  Its first attempt stopped on an optional Sentry CLI download; the corrected
  workflow resumes the existing checkout. See [capacity measurements](build-capacity.md).
- Current 45, 46 and 47 source candidates assemble reproducibly using the imported
  delta plus separate explicit compatibility patches. All skipped imported file
  changes have replacement patches or documented upstream equivalents.

## Outstanding before a release

1. Finish the bounded reference capacity build and inspect its diagnostics.
   Blacksmith has unlocked the account and the maintainer reports an unused
   3,000-minute allowance. A single hosted experiment is capped at 2,400 equivalent
   minutes; the installed PC runner remains available with its persistent cache.
2. Confirm cold-build disk/memory use and successful incremental resumption.
3. Finish the Discord integration ports to Electron 45, 46 and 47: their source
   candidates assemble, but Chromium/dependency patches and C++ still need builds.
4. Prove native streaming and Krisp behavior on the custom runtime.
5. Finish the shared nine-package generator, release ledger and AUR publisher.
6. Trace the live screen-share audio arguments and implement the verified native
   audio adapter; no unproven per-app fix is enabled.

The standard GitHub Actions workflow runs checks. The local reference workflow
uses four CPUs, 12 GiB RAM, up to 8 GiB additional swap and two compiler tasks.
The separate Blacksmith experiment uses 16 CPUs, 64 GB RAM and 12 compiler tasks
within the reported free allowance. Recurring Blacksmith compilation is disabled;
no AUR key is configured and no public binary is advertised.

## Probe incident

The bundled Discord executable ignored `ELECTRON_RUN_AS_NODE` during one local
probe and launched its normal bootstrap/profile instead. It was stopped. Future
runtime probes must set an isolated `DISCORD_USER_DATA_DIR`, disable downloads
where appropriate and never assume the Electron RunAsNode fuse is enabled.
