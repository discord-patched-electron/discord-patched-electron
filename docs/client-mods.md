# Vencord and Equicord

You can use either [Vencord](https://vencord.dev/download/) or
[Equicord](https://docs.equicord.org/installing/) with this app. Install one at a
time using its official installer.

## Install

1. Close Discord completely.
2. Run the installer and choose **Custom Location**.
3. Select the app folder from the table below, install the mod and restart Discord.

| Installation        | App folder                                            |
| ------------------- | ----------------------------------------------------- |
| Arch, Debian or RPM | `/opt/discord-canary-prerelease-patched-electron-bin` |
| Tarball             | The extracted folder containing `launch.sh`           |

Select the app folder itself, not its `runtime/` or `resources/` subfolder.

For the command-line installer, specify the same folder with `-location`:

```sh
chmod +x ./VencordInstallerCli-linux
./VencordInstallerCli-linux -install -location /absolute/path/to/app
```

For Equicord, replace `VencordInstallerCli-linux` with `EquilotlCli-linux`.
System packages may require running the installer with `sudo` from your normal
account. Always launch Discord as your normal user.

## Update or remove

Before updating Discord, uninstall the mod through its installer. Update Discord,
then install the mod again. For tarballs, extract the new version into a fresh
folder before installing the mod there.

To remove a mod or switch between them, use the current mod's **Uninstall** option.
The CLI equivalent is `-uninstall -location /absolute/path/to/app`. Removing a mod
leaves its settings in place.
