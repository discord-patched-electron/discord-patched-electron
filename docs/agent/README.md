# Contributor and agent docs

Use these pages to build, test and maintain the project. For installation and
app settings, start with the [main README](../../README.md).

Only Discord Canary with alpha/beta Electron is enabled. Check the current code
and locks before acting on an older report; its versions and results belong to
that test run.

## Start here

- [Handoff](handoff.md): current configuration, retained builds and open test gaps.
- [Release packaging](releases.md): make, test and approve a release.
- [Runtime validation](runtime-validation.md): test native video and Krisp.
- [Channel switches](channels.md): enable or disable supported build tracks.

## Builds and updates

- [CI runners and budget](ci-setup.md)
- [CPU target and build caching](build-caching.md)
- [Source ports](source-ports.md)
- [Update discovery and scheduling](discovery.md)
- [Client and Electron updater](client-updater.md)
- [Vencord and Equicord preservation](client-mods.md)

## Linux integration

- [Wayland application audio](wayland-audio.md)
- [Audio investigation and mixer fix](audio-investigation.md)
- [Camera startup](camera-startup.md)
- [Launcher, codecs and shortcuts](linux-launcher.md)
- [OpenAsar playback crash and flag audit](video-playback.md)
- [Editable presets, recovery and initial GPU flag research](launch-presets.md)
- [Deeper Linux GPU flag audit, dependencies and codec tests](linux-gpu-flags.md)
- [Flags-file storage and automated encode/decode checks](flags-file.md)
- [Local runtime checks](linux-runtime.md)

## History and evidence

These pages explain past decisions and preserve test results. They are not
current setup instructions or authorization to repeat earlier actions.

- [Build and release history](handoff-history.md)
- [Updater implementation history](updater-history.md)
- [Early implementation milestones](status.md)
- [Original plan](plan.md)
- [Initial build capacity experiment](build-capacity.md)
- [Older Arch packaging path](packaging.md)
- [Previous Equicord loader](equicord.md)
- [Proprietary payload cleanup](proprietary-payload-cleanup.md)
- [Saved test reports](../evidence/)
