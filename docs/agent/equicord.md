> Historical implementation, replaced on 24 October 2026. Current packages use
> [the official installer](../client-mods.md) with no custom mod loader. The earlier
> helper and environment variables below apply only to old local builds.

# Optional Equicord

Equicord is installed for the local Canary test app. The official published
`desktop.asar` is downloaded from Equicord's GitHub release and verified against
GitHub's SHA-256 asset digest before activation. Installation metadata records
release/asset IDs and checksum; a mutable `latest` tag is never trusted by itself.

Install or refresh for Canary:

```sh
python integration/launcher/equicord.py install \
  --home "${XDG_CONFIG_HOME:-$HOME/.config}/discord-canary-patched-electron/equicord"
```

For installed packages, the same helper is under
`/usr/lib/discord-canary-prerelease-patched-electron-bin/equicord.py`.
Use `stable` or `ptb` in the configuration path for the other channels.

The launcher activates only an explicitly installed mod. Equicord's own settings
live inside this app's separate profile (`equicord/`), and its updater can update
its user-owned ASAR. A user-owned loader copies the small prepared Discord
bootstrap on launch; no packaged ASAR is renamed or overwritten. The copy refreshes
on package updates, keeping the storage, audio and camera adapters. Upstream host
repatching is disabled where supported, and our Discord updater has no host/module
updates to intercept. Native modules continue to come from the qualified package.

`DPE_EQUICORD=0 launch.sh` starts without Equicord. Removing the `enabled` file in
the installation directory also disables it. `DPE_EQUICORD_HOME` selects a different
bundle; `EQUICORD_USER_DATA_DIR` selects a different settings directory.

Practical checks use fresh profiles, reach the real Canary login form, confirm
Equicord's renderer/preload and DiscordNative, then repeat after relaunch. No login,
message or call is used. This verifies injection compatibility, not every optional
Equicord plugin or future remote Discord UI update. The client update workflow
runs the same launch checks against the current official Equicord release.

References: [Equicord installation](https://docs.equicord.org/installing/),
[upstream loader](https://github.com/Equicord/Equicord/blob/main/src/main/patcher.ts),
[Equilotl installer](https://github.com/Equicord/Equilotl).
