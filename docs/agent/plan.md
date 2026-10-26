# Original research and implementation plan

This is a summary of the 23–24 October 2026 plan, retained to explain the project's
starting decisions. It is not the current build policy or task list.

The original scope included Stable, PTB and Canary, separate profiles and fully
automatic AUR publication. Later decisions selected **Canary only**, the standard
Canary profile, client downloads at launch and **manual release approval**.
Use [the handoff](handoff.md), [channel settings](channels.md) and
[release guide](releases.md) for current work.

## Intended result

Run Discord's official desktop client on a newer Electron while preserving its
native streaming API and Krisp. Users install binaries; they do not compile
Chromium. Application-audio routing should use the native client rather than
replace it with a browser client.

The design separated expensive native builds from client packaging. Identical
runtime inputs should compile once, then serve every compatible client.
Client/module changes should be checked independently and keep the last working
package available when a new combination fails.

The CPU target was x86-64-v3 with generic tuning. Completed artifacts and Ninja
objects were to be preserved, with compiler caching added only where effective.
Two early questions needed measurement: whether a full build fit the available
capacity, and whether Discord's private voice module worked with the ported bridge.

## Research inputs

### Public Electron bridge

Discord's [Electron fork](https://github.com/discord/electron) contains more than
the `discord/` directory: build wiring and Chromium/Node/FFmpeg patches are also
part of the integration. Copying an npm module into stock Electron is insufficient.

The inspected [module registration](https://github.com/discord/electron/blob/44-x-y/discord/module.cc)
exposes `electron_renderer_discord` and installs a Blink-backed media stream.
The [public frame header](https://github.com/discord/electron/blob/44-x-y/discord/public/electron_video_shared.h),
[video source](https://github.com/discord/electron/blob/44-x-y/discord/discord_video_source.cc)
and [video implementation](https://github.com/discord/electron/blob/44-x-y/discord/electron_video.cc)
define the interface and lifetime behavior that a port must preserve.

Research pins:

- Discord `44-x-y`: `aade2a4ea35e538d1be93207233c4269459a4561`.
- Inspected upstream base: `fd148b9908b83c8dc09924e45e6df44b83b6bfe7`.

Verify ancestry when importing a new revision; branch names can move.
[Source ports](source-ports.md) describes the implemented patch layout.

### AUR packaging and Krisp

The starting point was
[`discord_arch_electron`](https://aur.archlinux.org/packages/discord_arch_electron),
including its launcher, resource-path fixes, local module loading, updater policy
and Krisp patcher. The inspected recipe was `1:1.0.164-1`.

The upstream [Krisp patcher](https://aur.archlinux.org/cgit/aur.git/plain/krisp-patcher.py?h=discord_arch_electron)
uses Capstone and pyelftools to locate a signature check, recognizes an already
patched module and rejects unknown layouts. The plan retained that implementation
and attribution, preserved unstripped native binaries, and checked each channel's
own module instead of copying offsets between versions.

The alternative
[Canary recipe](https://github.com/ninetailedtori/Improved-PKGBUILDs/blob/discord-canary-electron-bin/PKGBUILD)
was research material. Live version lookups during packaging and fake-home
bootstrapping were unsuitable for fixed, reproducible release inputs.

### Private voice module

The official client installs `createDiscordStream` through Electron's Discord
API. Its separate `discord_voice.node` remains proprietary. Node-API exports do
not prove compatibility with private Electron interfaces; `electron-rebuild`
cannot recreate that module without source.

The inspected Stable 1.0.166 voice archive had SHA-256
`483445e794147f77c13848028cfaab2896f72d61f21c6f6ac7961ca0cea188d8`.
Its wrapper exposed source-selection methods, and static inspection found
`soundsharePid`, loopback and PulseAudio stream-monitoring paths. Those findings
justified an isolated test; they did not prove correct live-client arguments or
audio isolation. See [the audio investigation](audio-investigation.md).

## Source assembly and build reuse

Keep imported Discord changes separate from reviewed compatibility patches:

1. Pin an upstream Electron commit and the verified Discord import base.
2. Apply the ordered patch series to a disposable checkout.
3. Sync pinned dependencies and apply Electron's dependency patches.
4. Reject unknown conflicts or changed checksums before compilation.
5. Record the assembled source tree, toolchain, GN arguments and archive hash.

Do not silently skip patches, choose a blanket conflict side or assume that
patch/minor versions need no review. Chromium API changes still require porting.
The distribution target is `electron:electron_dist_zip`; `electron-builder`
packages apps and cannot replace that compilation.

The planned identities separated native inputs, helper inputs, integration and
tests. Documentation edits and polling timestamps must not force a compiler run.
A new test can validate an existing runtime; a publication retry can reuse an
existing release. A changed compiled patch requires a new runtime identity.

Durable JSON state belongs on `ci-state`, without merges into development branches.
Reserve work before dispatch, retain failed attempts, reconcile interrupted jobs
and avoid empty commits on unchanged polls. Immutable assets and their manifests
provide evidence of completed builds; caches are disposable.

## Capacity decisions

Early work used a containerized PC runner and a bounded Blacksmith experiment.
The experiment did not establish recurring free capacity. Provider selection had
to account for checkout disk, peak RAM, compile/link time, retention and billing
conversion, rather than advertised CPU minutes alone.

The plan considered standard GitHub runners for checks and packaging, and
Blacksmith, Depot or donated hardware for compilation. Sponsorship, paid storage
and unverified free allowances were not assumed. New policy is documented in
[CI runners](ci-setup.md); the original measurements remain in
[build capacity](build-capacity.md).

## Validation goals

- **Assembly:** patch ordering, expected bindings, dependency changes and exact
  source identity.
- **Runtime:** native module loading, synthetic frame delivery, callbacks and teardown.
- **Client:** actual fresh-profile launch and native streaming on each enabled
  runtime/client combination. A pass for one channel does not qualify another.
- **Krisp:** noise reduction with speech retained, disabled/enabled comparisons,
  reconnects and operation during screen sharing.
- **Audio:** two distinct application tones, selected-app isolation, Discord
  exclusion, source changes, cancellation, multi-process apps and cleanup.
- **Packaging:** dependency checks, file inspection, installation and launch in a
  clean distribution environment. Dummy runtimes only establish package structure.

Wayland became the primary target on 23 October. X11 checks alone could not
complete audio validation. The selected video source needed a verified relation
to audio processes; titles, focus and a PipeWire video node ID were insufficient.
Unknown identity must leave audio off rather than silently share system sound.

[Vesktop's audio integration](https://github.com/Vencord/Vesktop/blob/main/src/main/venmic.ts)
and [picker](https://github.com/Vencord/Vesktop/blob/main/src/renderer/components/ScreenSharePicker.tsx)
informed the design. The implemented helper uses installed PipeWire tools and
Discord's native PID input. It does not ship venmic or replace the native video path.
See [Wayland audio](wayland-audio.md) for the final routing and evidence.

## Packaging and publication goals

Generate recipes from one reviewed template with fixed URLs, checksums and
channel-specific metadata. Keep credentials out of compilation jobs. Publish
immutable release assets, verify their public downloads, then update AUR with
PKGBUILD and `.SRCINFO` together. Serialize publication and reject downgrades.

AUR failures should retry publication without compiling or bumping an already
published version again. Never force-push generated recipes or overwrite public
assets. The original automatic-approval and package-version proposals were later
replaced by the [current release policy](releases.md).

## Outcome and remaining questions

The reference and Electron 46 ports subsequently compiled. Native frame,
offline Krisp, application-audio fixtures and package checks passed; evidence is
indexed in [the handoff](handoff.md). Camera work also became an independent
helper, documented in [camera startup](camera-startup.md).

Those results do not establish live microphone/Krisp behavior during a share,
all desktop/GPU combinations, future private-module compatibility or a sustainable
cold-build cadence within the hosted allowance. Keep those limits visible when
assessing a new release.
