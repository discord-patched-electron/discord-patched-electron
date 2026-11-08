# Microphone guard and the complete native preparation chain

The microphone hotplug guard changes the voice module's complete hash before
H.264 HRD and GPU capture-dimension corrections run. Those later patches still
recognized only the earlier buffer-only Pulse hashes, so the new preparation
could be rejected before publishing its immutable generation.

The HRD and dimension patches now recognize exact complete-file identities for
both reviewed native versions with the microphone guard applied. Their existing
instruction sites, expected output hashes, unknown-byte rejection and atomic
replacement behavior remain unchanged. Prepared final variants are recognized
for repeat preparation. No hash guard was relaxed and no patch order changed.

Evidence is in `out/native-patch-chain/`. `hashes.json` records the three before/
after identities for each native version; `report.json` records actual full
`patch_app_audio` preparation from earlier prepared modules, guarded unprepared
modules and already complete guarded modules. Each final result was reprocessed
without changing bytes. The current final module has SHA-256
`0682ab8c93c51c8c36debc102c829b4177f16ff4fae4cab2e901d6ee341461e4`.

The executable ELF fixture now exercises the combined Pulse, HRD and dimension
pipeline, preserves a hardlinked input, repeats preparation, and rejects an
unrelated trailing-byte modification. Twenty-eight scoped tests passed. The real
current module also passed `client_native.prepare`, but that check only verified
that scope files were generated. It did not require capture/encoder scopes to
report `supported: true`; they were unsupported because the guard changed the
complete hash. The [decoder follow-up](decoder-buffered-output-20261103.md) adds
the missing exact scope identities and verifies every scope is supported and
bound to the actual final native hash. No profile, account or running
module was copied or modified.

Candidate staging must copy **all three** of `patch_pulse_audio.py`,
`patch_vaapi_hrd.py` and `patch_capture_dimensions.py` into `app/scripts/`.
Normal startup's compatibility key already hashes these scripts. It can then
publish the new immutable native generation and its regenerated scope records.
Replacing only `discord_voice.node` remains incorrect because it would leave
stale scope hashes.
