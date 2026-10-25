# Vencord and Equicord

Launch Discord once, then close it and run the official
[Vencord](https://vencord.dev/download/) or
[Equicord](https://docs.equicord.org/installing/) installer. Choose **Canary**
from its detected installations. Install one mod at a time.

The installation uses Discord's usual path:
`~/.config/discordcanary/app-<version>/resources`. The launcher detects Vencord or
Equicord there and preserves it across Discord, Electron and package updates.
No reinstall or root access is needed.

Both mods keep their own updater. Client updates keep using your installed mod
files, including newer versions downloaded by the mod, and preserve its settings.

If you override `XDG_CONFIG_HOME` or `DPE_PROFILE_DIR`, choose **Custom Location**
and select that profile directory. Some installers only search `~/.config`.

To remove a mod or switch between them, use its installer's **Uninstall** option.
Your mod settings stay in place.
