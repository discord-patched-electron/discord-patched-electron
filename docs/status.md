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

## Outstanding before a release

1. Verify the containerized local GitHub Actions runner end to end. Blacksmith
   suspended the account; the maintainer explicitly chose their PC instead.
2. Run the bounded reference capacity build on the storage SSD.
3. Port the Discord integration to Electron 45, 46 and 47. Source probes currently
   stop on conflicts, including moved sandboxed renderer exports and Chromium
   patch changes. A textual apply check does not validate dependency patches or C++.
4. Prove native streaming and Krisp behavior on the custom runtime.
5. Finish the shared nine-package generator, release ledger and AUR publisher.
6. Trace the live screen-share audio arguments and implement the verified native
   audio adapter; no unproven per-app fix is enabled.

The standard GitHub Actions workflow runs checks. The manual reference build uses
the local container with four CPUs, 12 GiB RAM, up to 8 GiB additional swap and two
compiler tasks. No paid compiler is configured, no AUR key is configured and no
public binary is advertised.

## Probe incident

The bundled Discord executable ignored `ELECTRON_RUN_AS_NODE` during one local
probe and launched its normal bootstrap/profile instead. It was stopped. Future
runtime probes must set an isolated `DISCORD_USER_DATA_DIR`, disable downloads
where appropriate and never assume the Electron RunAsNode fuse is enabled.
