# Previous Equicord loader

The custom loader was replaced on 24 October 2026. Current packages use the
[official Vencord or Equicord installer](../client-mods.md), and
[the updater preserves it](client-mods.md). Do not use the old helper commands
with a current package.

## How the older loader worked

The local Canary app downloaded Equicord's published `desktop.asar` and checked
GitHub's SHA-256 asset digest before activation. Metadata recorded the release,
asset ID and checksum rather than trusting a mutable `latest` tag alone.

A user-owned loader copied the prepared Discord bootstrap on launch and refreshed
it after package updates. This kept the storage, audio and camera adapters without
renaming the package's ASAR. Equicord settings lived in that app's separate profile,
and its own updater could replace the user-owned mod payload.

The old launcher recognized `DPE_EQUICORD=0`, an `enabled` marker and
`DPE_EQUICORD_HOME`. These controls belong to the retired implementation.
`EQUICORD_USER_DATA_DIR` selected the settings location.

## Validation at the time

Fresh-profile checks reached the real Canary login form, confirmed Equicord's
renderer/preload and DiscordNative, and repeated after relaunch. They used no
account, message or call. This established injection compatibility, not support
for every plugin or later Discord web update.

References: [Equicord installation](https://docs.equicord.org/installing/),
[upstream loader](https://github.com/Equicord/Equicord/blob/main/src/main/patcher.ts),
[Equilotl installer](https://github.com/Equicord/Equilotl).
