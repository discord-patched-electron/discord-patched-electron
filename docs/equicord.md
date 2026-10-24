# Equicord

Equicord is optional. Install it for Canary with:

```sh
python3 /opt/discord-canary-prerelease-patched-electron-bin/equicord.py install \
  --home "${XDG_CONFIG_HOME:-$HOME/.config}/discord-canary-patched-electron/equicord"
```

For a tarball, run `python3 ./equicord.py install` from the extracted folder with
the same `--home` argument. Arch packages use `/usr/lib/` instead of `/opt/`.
Replace `canary` with `stable` or `ptb` for another channel.

Restart Discord to activate it. Running the command again refreshes the bundle.
The installer downloads the official release and verifies its SHA-256 digest.
Equicord's files live in your config directory and survive package updates.

Launch with `DPE_EQUICORD=0` to temporarily disable it. Remove the `enabled` file
in the directory above to keep it disabled. Its settings live in the app's
separate profile under `equicord/`.

The login form and relaunch are tested with Equicord. Individual plugins and
future Discord UI changes may still break. See [Equicord's docs](https://docs.equicord.org/)
and the [loader implementation notes](agent/equicord.md).
