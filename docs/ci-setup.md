# Free CI setup

Repository: `discord-patched-electron/discord-patched-electron`.
Ordinary checks run on free standard GitHub-hosted runners for this public repo.
Compilation requires substantially more disk than those runners provide.

## Blacksmith

1. Sign in at <https://app.blacksmith.sh> with GitHub.
2. Select the `discord-patched-electron` organization and install its GitHub App.
3. Grant access only to this repository. Skip the migration wizard: this project
   owns its workflow files. Do not enable sticky disks or Docker build caching.
4. Check the billing screen for the free allowance, runner-size conversion and
   what happens when the allowance runs out. A spending alert is not a hard cap.
5. Confirm these account details before enabling the capacity build. No payment
   method or paid plan is required by this project; do not add one for this step.

The runner integration uses Blacksmith's GitHub App, so it does not require a PAT
or a token pasted into chat. A future usage-reporting token, if needed, must be
entered through a private secret prompt or directly in GitHub Actions secrets.

Blacksmith's current quickstart requires an organization-owned repository:
<https://docs.blacksmith.sh/introduction/quickstart>.
The documented runner inventory is at
<https://docs.blacksmith.sh/blacksmith-runners/overview>.
An 80 GB runner is below Chromium's documented disk requirement. Start with a
runner that actually fits, bounded by confirmed free-account capacity; slower is
acceptable but insufficient disk is not.

Do not enable recurring compilation until one cold build establishes disk/RAM,
elapsed time and allowance consumption. If sustainable free capacity is not
available, ask the user how to proceed before choosing a paid service or cutting
the requested channels/release cadence.

## Later AUR setup

After runtime and package validation, create or select an AUR maintainer account
and register a dedicated SSH public key with it. Store the private key in the
repository's `AUR_SSH_PRIVATE_KEY` Actions secret, never in source or chat. Verify
the AUR host key through Arch's published information. The publisher will only
receive that credential in its separate publication job. No AUR submission is
appropriate before a working downloadable runtime exists.
