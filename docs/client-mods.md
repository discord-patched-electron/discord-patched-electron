# Vencord and Equicord

1. Launch Discord once, then close it.
2. Run **one** of these official installer commands in a Linux Bash terminal
   with `curl` installed. Don't use `sudo`.

   **[Vencord](https://vencord.dev/download/):**

   ```bash
   sh -c "$(curl -sS https://vencord.dev/install.sh)"
   ```

   **[Equicord](https://docs.equicord.org/installing/):**

   ```bash
   sh -c "$(curl -sS https://raw.githubusercontent.com/Equicord/Equicord/refs/heads/main/misc/install.sh)"
   ```

3. Choose **Canary** from its detected installations.
4. Launch Discord again.

**You only need to install your chosen mod once per Discord profile.** Discord,
Electron and package updates preserve the installed mod and its settings, so you
don't need to reinstall it after updates. Both mods can still update themselves.

## If Canary isn't detected

Choose **Custom Location** and select your profile
directory, normally `~/.config/discordcanary`. This may be needed with a
[custom profile](linux-launcher.md#profiles).

## Uninstall or switch mods

Choose **Uninstall** in the mod's installer. To switch mods, uninstall the current
one first. Your mod settings stay in place.
