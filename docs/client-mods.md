# Vencord and Equicord

Launch Discord once, then close it and run the official
[Vencord](https://vencord.dev/download/) or
[Equicord](https://docs.equicord.org/installing/) installer. Choose **Canary**
from its detected installations. Install one mod at a time.

The installation uses Discord's usual path:
`~/.config/discordcanary/app-<version>/resources`. Automatic updates preserve
the mod loader and refresh the underlying bootstrap. No root access is needed.

If you override `XDG_CONFIG_HOME` or `DPE_PROFILE_DIR`, choose **Custom Location**
and select that profile directory. Some installers only search `~/.config`.

To remove a mod or switch between them, use its installer's **Uninstall** option.
Your mod settings stay in place.
