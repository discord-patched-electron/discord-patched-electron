# Equicord

Use [Equicord's official installer](https://docs.equicord.org/installing/).
Equicord is optional and isn't bundled with these packages. No project-specific
installer is needed.

Close Discord completely. In the installer, choose **Custom Location** and select
the app directory containing `resources/`:

| Installation  | Canary directory                                          |
| ------------- | --------------------------------------------------------- |
| Debian or RPM | `/opt/discord-canary-prerelease-patched-electron-bin`     |
| Arch          | `/usr/lib/discord-canary-prerelease-patched-electron-bin` |
| Tarball       | The extracted directory containing `launch.sh`            |

Replace `canary` with `stable` or `ptb` for another channel. Select the app
directory itself, not `runtime/` or `resources/`. Automatic installer discovery
doesn't recognise our package names.

The official Linux CLI accepts the same location. For a user-owned tarball:

```sh
chmod +x ./EquilotlCli-linux
./EquilotlCli-linux -install -location /absolute/path/to/extracted-app
```

System packages need write access to their installation directory. Run the
official installer through `sudo` from your normal account when needed, using
the matching `/opt/` or `/usr/lib/` path. Keep launching Discord as your normal
user.

## Updates and removal

Equicord manages its own bundle updates. Its settings use the app's separate
profile under `equicord/`. A Discord package update replaces `app.asar`, so rerun
the official installer against the updated app to reapply Equicord. Extract new
tarballs into a fresh directory, then install Equicord there.

To disable or remove it, use **Uninstall Equicord** in the official installer, or
run the CLI with `-uninstall -location` and the same app path. Reinstall it when
you want to enable it again.

Older local test builds used `equicord.py` and `DPE_EQUICORD`; new packages don't.
Install Equicord with the official installer after switching to a new package.
Existing settings are kept, but the old helper's `enabled` marker is no longer read.

CI tests installation, login-form launch, relaunch and removal with the official
installer. It checks that uninstall restores the original ASAR before packaging.
Individual plugins, logged-in behavior and future Discord UI changes still need
their own testing.
