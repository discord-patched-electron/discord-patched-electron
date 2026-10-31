# Independent baseline verdicts

Recorded before opening the corrective implementation or investigation notes.
HEAD: `f90b251caee6b4e7d265c0e450e53a70d97b0aba`; checkout initially clean.

Both supplied reproduction scripts completed successfully under
`unshare --user --map-current-user --net env UV_OFFLINE=1 uv run python`.
Only synthetic archives and disposable OpenPGP keys were used. Release API and
command boundaries were mocked. No remote approval was performed.

## Signed release binding: qualifies

`repro_release_binding.py` exercised the complete `releases.approve(check_only=True)`
path. A manifest edited after signing passed integrity and provenance checks with
the old signed checksum list. Baseline `scripts/release_signing.py::verify` verifies
the signatures over the Arch archive and checksum text, but never compares the
checksum text to release.json or the other assets. `scripts/releases.py::approve`
checks package hashes against the edited manifest and upload digests against the
mocked current asset metadata; those checks do not restore the missing signature
binding. A partial/stale release copy or a pre-publication manifest edit provides a
realistic trigger. An arbitrary local modification after download alone would
still fail the independent upload digest check; this reproduction correctly
models matching published bytes and upload digests.

The changed minimum_glibc field is enough to demonstrate unauthenticated manifest
metadata acceptance; other manifest changes must still satisfy the independent
approval checks. This is not proof of remote code execution or unrestricted
approval bypass. The impact is that approval can bless metadata or non-Arch assets
not covered by the signer's checksum list. docs/agent/releases.md, Release
signatures, requires pinned-key verification before approval and immutable signed
manifests. Its compatibility allowance for entirely unsigned older releases does
not excuse this behavior in a signing-required release. No documented limitation
permits stale checksums for signed releases. Verdict: concrete integrity defect.

## Archive link relocation: qualifies

`repro_archive_relocation.py` extracted `files/entry.js -> ../files/value.js` into a
staging directory named files. It initially read the synthetic archive content;
after rename it read an unrelated synthetic sibling and resolved outside the
published module. `integration/updater/client.py::extract_module` checks containment
using the staging directory's current absolute name. `install_module` stages into
files and publishes to a different cache name, so the production operation has
the same relocation boundary. Without the sibling, the link becomes dangling;
with a matching sibling, the target lies outside the verified module.

This requires an unusual symlink layout in a checksum-pinned downloaded archive;
it is not an unauthenticated network injection. Nevertheless the extractor's
comment promises internal relative links and docs/agent/client-updater.md, Client
downloads and approval, promises rejection of external links. Neither limits the
promise to the pre-publication pathname. The intended compatibility with ordinary
internal relative links should be preserved. Verdict: concrete relocation and
containment defect, with deterministic missing/wrong-file impact.

Logs: baseline-release.log and baseline-archive.log in this directory.
