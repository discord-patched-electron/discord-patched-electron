# CI channel switches

Edit the two channel lists in
[`config/channels.json`](../../config/channels.json) to choose what discovery,
builds and packaging support. A listed channel is enabled. Current defaults are:

```json
{
  "discord_channels": ["canary"],
  "electron_channels": ["prerelease"]
}
```

| Setting             | Available options                 | Enabled      |
| ------------------- | --------------------------------- | ------------ |
| `discord_channels`  | `stable`, `ptb`, `canary`         | `canary`     |
| `electron_channels` | `stable`, `prerelease`, `nightly` | `prerelease` |

Discord Stable and PTB are disabled because Canary has better compatibility with
newer Electron versions. Electron's `prerelease` track means alpha and beta builds.
It brings Chromium's ongoing Wayland and Linux improvements sooner. Stable and
nightly Electron remain available options, but are disabled.

Use **alpha/beta Electron** in user-facing prose. Use **test release** for a project
package awaiting approval as stable. GitHub displays the latter as **Pre-release**.
These are separate concepts. Package names retain the existing `prerelease` track
identifier so installed packages and profiles keep working.

## How the switches work

Discovery fetches only enabled channels. Client changes start an independent
release job for each enabled Discord channel. A successful compiler run starts
**Qualify compiled runtime**, which tests and packages every enabled Discord channel.
**Release Linux packages** also accepts a single `discord_channel` for manual work.
Disabled channels are rejected before downloading or packaging.

Electron discovery and scheduling use the enabled tracks. The coordinator selects
the newest eligible version above the approved runtime, retains the four-day
cooldown and security exceptions, and passes the track through source assembly,
compilation, package names and AUR recipes. There is one shared approved runtime
for client-only updates, not a separate automatic release stream for each track.

To try another Electron track, add it to the list and use **Candidate Electron
build** with its name as `target`. Keep the existing track enabled until the
replacement has passed checks and been approved. Approval refuses a downgrade.
Enabling a track does not make an unsupported upstream version compatible.
Its source lock and patch queue still have to pass assembly and runtime checks.

After enabling a Discord channel, run **Discover upstream releases** to refresh
the client snapshot before packaging it. Historical locks alone do not establish
current compatibility. Each channel still needs practical testing before approval.

Changing the lists does not invalidate native compiler objects. Package and
integration checks run again, using the retained runtime where possible.
