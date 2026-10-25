# Proprietary payload cleanup — 25 October 2026

The user authorized removal of full proprietary Discord client blobs from source,
Git history, Actions artifacts and releases, plus approval of the latest build.

## Results

- Scanned reachable history paths and all 2,140 local Git objects after fetching
  remote branches and tags; also checked the PR head. No full Discord client ASAR,
  native module or downloaded client archive was found. No history rewrite was
  needed. Public Electron patches, MIT OpenAsar sources, project artwork and
  small compatibility match excerpts remain.
- Deleted four legacy bundled-client releases and their assets. Deleted 19 legacy
  package artifacts totaling 18,239,561,566 bytes. IDs are recorded in the
  [removal evidence](../evidence/proprietary-payload-cleanup.json).
- Inspected all 440 other Actions artifact ZIPs. None contained full client
  payloads. The 50 native binary matches were this project's camera probe helper.
  Actions caches contain development tools and open-source dependencies.
- Retained the verified Electron runtime cache. Its `default_app.asar` is
  Electron's open-source default application, not Discord's client. No Discord
  native modules or client archive are present in that runtime ZIP.
- Removed obsolete release and abandoned candidate tags. Kept the approved
  release tag immutable; future tags use fixed-width UTC schema 3.

The client-free release `v20261025.223908.0-canary.041bb4c70d3f760e` is now
approved, marked Latest and published to AUR by approval run `40243186523`.
Its release qualification run `40241194140` passed. Existing package validation
rejects client payloads in staging and in the generated release formats before
upload. The client is fetched from Discord on the user's machine.

Deletion removes repository-visible GitHub release assets and Actions artifacts.
It cannot erase copies already downloaded by other people or GitHub's internal
retention/backups. Local ignored development downloads are not Git history or
published source and were not part of this remote-storage cleanup.
