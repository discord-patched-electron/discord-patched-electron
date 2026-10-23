# Discord Electron: research and implementation plan

Research checked: 23 October 2026. Status: implementation plan; no custom runtime has been built or proven compatible yet.

## 1. Intended result and decisions

Build a current Electron with Discord's native streaming extensions, run the official Discord desktop client on it, and publish nine automatically maintained AUR binary packages covering Discord Stable, PTB and Canary on each of three Electron channels. Compile everything that needs compilation in CI. Users should only download and install binaries.

Interpret **stable, prerelease and nightly as Electron channels**, independent of Discord's **stable, PTB and canary client channels**. Support the full three-by-three combination. Pin each Discord channel's own client and modules; never substitute Stable payloads into a PTB or Canary package.

Package names use `discord-<discord-channel>-<electron-channel>-patched-electron-bin` (confirm AUR availability before submission). The Discord channels are `stable`, `ptb` and `canary`; Electron channels are `stable`, `prerelease` and `nightly`. The repository is `discord-patched-electron/discord-patched-electron`, in the organization selected by the maintainer.

Each package is a usable Discord installation with its own runtime and launcher. Also publish standalone runtime archives. No extra integration package is required.

Nine packages must not imply nine Chromium compilations. Build once per distinct runtime key, then reuse that artifact across the three Discord channels. Only a proven difference in compiled compatibility patches should create a separate runtime variant. Keep discovery, packaging and publication driven by channel configuration and one template.

Use one small public GitHub repository, GitHub Actions for orchestration, the maintainer's containerized self-hosted compile runner, GitHub Releases for binary downloads, and AUR Git for package recipes. Keep the build scripts runner-independent; provider preference is secondary to adequate free capacity. If no suitable ongoing free CI is available, pause the dependent build/deployment work and ask the user how to proceed. Do not silently choose a paid plan or reduce the requested release coverage.

Base the packaging on the current `discord_arch_electron` recipe, launcher and existing compatibility patches, adapting its established behavior to the custom runtime and nine channel combinations. Working **Krisp noise suppression is required**, alongside native streaming and application-specific screen-share audio. Reuse the existing Krisp patcher rather than inventing a replacement.

Preserve native Discord streaming. Do not substitute the browser/WebRTC fallback or turn this into a Vesktop-based client. Aim to fix application audio in a native helper or the module wrapper, without changing the remote Discord UI.

**Two feasibility gates must be resolved early:** a full build must fit available free CI resources, and the shipped private voice module must work with the transplanted public Electron integration. Automation cannot establish either by itself.

## 2. What the research establishes

### Discord's public Electron integration exists

Discord publishes an [Electron fork](https://github.com/discord/electron). Its release branches matter; do not assume the default branch matches the shipped app. The inspected `37-x-y`, `42-x-y` and `44-x-y` branches contain Discord-specific integration.

The inspected [44-x-y Discord source directory](https://github.com/discord/electron/tree/44-x-y/discord) contains the native video bridge. [`module.cc`](https://github.com/discord/electron/blob/44-x-y/discord/module.cc) registers the `electron_renderer_discord` linked binding and exposes an installer for a Blink-backed media stream. Renderer exports and Node binding registration connect it to Electron. This is built into Electron; copying an npm module into an official Electron installation is insufficient.

The public bridge also includes frame delivery and native video interfaces. Preserve the interface layout and lifetime rules in [`electron_video_shared.h`](https://github.com/discord/electron/blob/44-x-y/discord/public/electron_video_shared.h), together with [`discord_video_source.cc`](https://github.com/discord/electron/blob/44-x-y/discord/discord_video_source.cc) and [`electron_video.cc`](https://github.com/discord/electron/blob/44-x-y/discord/electron_video.cc). Discord's changes include build wiring and dependency patches, not just the `discord/` directory.

Research snapshot for later reproduction, not a permanent build target:

- Discord `44-x-y` commit: `aade2a4ea35e538d1be93207233c4269459a4561`.
- Inspected common base with upstream: `fd148b9908b83c8dc09924e45e6df44b83b6bfe7`.
- Recompute and verify ancestry when importing; branches can move. See the [fork comparison](https://github.com/discord/electron/compare/electron:44-x-y...44-x-y).

### The current AUR package is the packaging baseline

The [`discord_arch_electron` discussion](https://aur.archlinux.org/packages/discord_arch_electron) describes the missing custom stream API and reports `window.createDiscordStream is not a function`. Its comments link Discord's public native integration. This supports restoring the native bridge rather than assuming an upstream Electron upgrade alone fixes streaming.

The [current packaging recipe](https://aur.archlinux.org/cgit/aur.git/plain/PKGBUILD?h=discord_arch_electron), inspected at `1:1.0.164-1`, supplies the baseline: distribution extraction, ASAR/resource-path changes, desktop identity, local module loading and disabled desktop updater. It also patches `discord_krisp.node`, creates `modules/discord_krisp/KMS/logs` for initialization, and preserves native binaries with `options=('!strip')`. The new recipes must retain these behaviors, adapted to each installation path and channel. The modules are downloaded proprietary binaries, not rebuilt from public source.

The packaged [Krisp patcher](https://aur.archlinux.org/cgit/aur.git/plain/krisp-patcher.py?h=discord_arch_electron) uses pyelftools and Capstone to locate a Discord-signature check during Krisp initialization and patch the conditional branch. It recognizes an already-patched case and otherwise fails if it cannot find the expected pattern. Preserve its provenance and inspect its symbol/offset assumptions against each supported binary; do not replace it with fixed offsets copied between module versions.

The current [install script](https://aur.archlinux.org/cgit/aur.git/plain/discord_arch_electron.install?h=discord_arch_electron) confirms that Krisp patching moved to package-build time. Older instructions involving `PATCH_KRISP=true` and patching downloaded modules on startup are historical, not the baseline for this project. Preserve the [launcher's flags-file support](https://aur.archlinux.org/cgit/aur.git/plain/discord-launcher.sh?h=discord_arch_electron), adapting names and executable paths for each package.

The [`discord-canary-electron-bin` discussion](https://aur.archlinux.org/packages/discord-canary-electron-bin) links an [alternative recipe](https://github.com/ninetailedtori/Improved-PKGBUILDs/blob/discord-canary-electron-bin/PKGBUILD). Use it as research, not an unchanged production template: live version lookups inside a PKGBUILD and bootstrapping through a fake home directory are poor fits for deterministic automated releases.

### The voice module is a separate boundary

Inspection of the official stable distribution found that its core preload installs `createDiscordStream` through the Electron `discord` API. The separately shipped `discord_voice.node` remains a proprietary binary.

The inspected voice wrapper exposes `setDesktopSource(id, videoHook, type)`, `setDesktopSourceWithOptions(options)`, `setGoLiveDevices(options)` and corresponding cleanup paths. Binary strings include `soundsharePid`, `soundshareId`, `soundshareLoopback`, `application.process.id`, and Linux PulseAudio soundshare code paths. This makes a source-specific audio fix plausible; strings do not prove which arguments the Linux client supplies or what the binary accepts.

Reproduction input: the [official stable Linux x64 manifest](https://updates.discord.com/distributions/app/manifests/latest?channel=stable&platform=linux&arch=x64) resolved during research to Discord `1.0.166`. The inspected [voice distribution](https://stable.dl2.discordapp.net/distro/app/stable/linux/x64/1.0.166/discord_voice/1/full.distro) had SHA-256 `483445e794147f77c13848028cfaab2896f72d61f21c6f6ac7961ca0cea188d8`. Resolve and lock fresh inputs at implementation time.

Node-API symbols in a binary do not establish compatibility for its private Electron interfaces. `electron-rebuild` cannot recreate a proprietary module without its source. Native loading, calls, frame delivery and teardown all need validation.

## 3. Repository structure and conflict strategy

Keep source assembly disposable. The maintained repository contains only project-owned code, patch files and metadata:

```text
.github/workflows/
  check.yml                 # cheap validation on changes
  update.yml                # scheduled/manual discovery and orchestration
  build.yml                 # reusable compile, validate and publish workflow
config/
  channels.json             # Electron × Discord matrix, runner and budget policy
  sources.lock.json         # fork/tool pins and separate client/module channel locks
patches/
  discord/<major>/           # imported Discord integration and provenance
  compat/<major>/            # small ports to newer Electron/Chromium APIs
  audio/                    # our independent integration changes
integration/
  launcher/
  module-patches/
  audio-helper/
packaging/
  upstream/                 # pinned current AUR recipe, launcher, patcher and provenance
  PKGBUILD.in
  desktop.in
scripts/
  ci.py                     # discover, fingerprint, state, package, publish
  assemble.sh
  build.sh
tests/
  bridge/
  audio/
  pipeline/
docs/
  compatibility.md
  build-capacity.md
```

Use a separate `ci-state` branch containing small JSON records for attempts, reservations and publication status. It never merges into the development branch. Releases contain immutable build manifests and are the durable evidence that an artifact exists. Caches are disposable optimizations, never the only record of a completed build.

### Derive packaging from the existing recipe

Pin the AUR Git commit and import its PKGBUILD, launcher, Krisp patcher and relevant install behavior with attribution. Create the shared template from those files. Keep a short documented delta: custom runtime asset/path, channel parameters, isolated package identities, CI-generated versions/checksums and publication metadata. Preserve the upstream resource/bootstrap fixes and Krisp handling before adding new behavior.

Track later AUR changes as a small reviewed packaging update. Refresh the common baseline once and regenerate all nine recipes; do not maintain nine independently edited PKGBUILDs or repeatedly merge whole generated recipes. Preserve applicable fixes from the Canary work where its client layout differs. Remove obsolete migration messages when creating new package identities, rather than claiming they upgraded from the old package.

### Assemble from upstream, rather than repeatedly merge two forks

1. Resolve an exact upstream Electron tag to a commit. Fetch the required Git history for three-way patch application.
2. Import Discord's integration from a verified common base and exact fork commit. Inspect the commit range and dependency patch changes; do not blindly diff two unrelated branch tips, which could revert upstream fixes.
3. Keep the imported integration and our compatibility edits as separate ordered patch series. Record source commits, purpose, affected interfaces and required checks for each patch.
4. Apply the series onto a fresh upstream checkout. Use `git am --3way` where appropriate. Apply changes to Electron's dependency patch lists before its dependency patch application phase. Preserve ordering in Chromium/Node/FFmpeg `.patches` files.
5. Sync pinned dependencies and apply their patches through Electron's own machinery. Verify the resulting checkout before compiling.
6. When upstream already contains a change, remove it through a reviewed patch update with evidence. Never silently skip a failing patch.

Keep our audio routing code outside Electron internals wherever the bridge allows it. Isolate unavoidable Chromium/Blink differences behind a small adapter. Retain only the major-specific variants needed by the configured release streams and newly published maintenance releases.

Watch Discord's fork for improved ports, but make changes to the imported patch series reviewable. A new ordinary Electron release can build and publish automatically using the existing reviewed series. Updating the series itself is a source change with tests, not an unattended conflict-resolution heuristic.

For repeated conflicts, recorded, reviewed resolutions can help, but initially prefer explicit compatibility commits over a shared opaque `rerere` cache. Never use blanket `ours`/`theirs`, ignore failed hunks, or let an automated textual fix publish without validation.

**Expected maintenance:** patch/minor updates may apply cleanly; Chromium/Blink changes can require real porting. This design removes recurring merge bookkeeping, not upstream API changes. Nightly is likely to expose those changes first. On a conflict, record the affected release, preserve the last working package and resume after a fix.

## 4. Free CI feasibility and runner selection

**Implementation decision (23 October 2026):** Blacksmith suspended the account. The maintainer explicitly chose their PC as the runner. Use a dedicated Ubuntu container with only the build workspace mounted on `/mnt/storage`, four CPUs, 12 GiB RAM and two compiler tasks initially. Keep builds serialized and persistent so they can resume. See `docs/ci-setup.md` for deployment. The hosted-provider research below is retained for a possible future migration, not an active setup requirement. No Blacksmith key or quota policy is needed.

Evaluate Depot and Blacksmith on equal terms: sufficient disk/RAM/job duration, sustainable included compute, storage charges and enforceable usage limits. Start with the same build scripts on GitHub Actions-compatible runners; do not introduce a second orchestration platform just to switch providers. A time-limited trial can support a bounded feasibility experiment, but does not establish a free recurring service.

Blacksmith requires an organization-owned repository, so use the selected `discord-patched-electron` organization. Its spending alerts are not verified hard caps. [Quickstart](https://docs.blacksmith.sh/introduction/quickstart)

Blacksmith advertises 3,000 free minutes per month and no card required to start. Sticky disks and Docker layer caching are separately priced at $0.50/GB/month. Its OSS program has eligibility requirements and is not automatic approval. Do not enable paid storage or assume sponsorship. [Blacksmith pricing](https://www.blacksmith.sh/pricing)

The documented Ubuntu x64 configurations include 2 vCPU/8 GB RAM/80 GB disk, 8 vCPU/32 GB/160 GB and 16 vCPU/64 GB/750 GB. A slow 80 GB runner is not necessarily capable of this job. [Runner specifications](https://docs.blacksmith.sh/blacksmith-runners/overview)

Chromium documents at least 100 GB of disk for development; actual checkout, build, packaging and temporary space must be measured together. [Chromium Linux build requirements](https://chromium.googlesource.com/chromium/src/+/main/docs/linux/build_instructions.md)

### Initial capacity experiment

Before enabling recurring compilation:

1. Confirm each candidate provider account's actual free allowance, runner-size conversion, billing period, overage behavior and maximum job duration. Do not interpret Blacksmith's advertised allowance as 3,000 wall-clock minutes on every SKU, or Depot's trial as an ongoing free tier.
2. Confirm that the account cannot incur charges under the selected configuration. A workflow budget check is useful but is not a substitute for a provider-enforced billing limit where one is available.
3. Run one budget-bounded cold build on a runner with sufficient disk. Measure fetch time, peak disk/RAM, compile time, archive size and charged minutes. Stop if the reserved free allowance is insufficient.
4. Compare the smallest feasible size with a larger size only if free allowance remains. Choose the configuration consuming the fewest allowance units per successful build, not necessarily the fewest CPUs.
5. Estimate a month of Electron stable/prerelease/nightly builds, validation across all nine combinations, Discord channel updates and retries using observed numbers. Count shared runtime builds once; account separately for any required compiled variants. Record measurements and the account's confirmed conversion rules in `docs/build-capacity.md`.

For conservative planning, a 2-vCPU-equivalent accounting model would allow `3000 / (vCPUs / 2)` wall-clock minutes: 750 at 8 vCPU or 375 at 16 vCPU. Treat this calculation as a budget model until the account's actual rules are verified, not a promise of capacity.

Use the selected provider's usage reporting and hard caps where available. For Blacksmith, its [CLI](https://docs.blacksmith.sh/blacksmith-cli/usage) provides JSON usage/cost reporting and a runner catalog. Query account-wide usage for the actual billing period before launching compilation. Reserve the job's worst-case allowance based on its timeout, including other in-flight work and a safety margin. Reconcile after completion; fail closed if usage cannot be read reliably. Serialize compilation initially to simplify both budgeting and state.

When free capacity runs out, keep pending stable releases in the durable queue and report the delay. Prioritize stable over prerelease over nightly. Do not silently enable billing. If the evidence shows no suitable sustainable free CI, stop before allocating paid resources and ask the user to choose the next step. Present the measured requirements, available sponsorship/donated-runner options and any paid option's estimated cost. The user explicitly permits this pause; do not silently cut channels, skip stable releases or enter an indefinite quota-wait loop as a substitute for that decision.

### Other CI options considered

- **Standard public GitHub Actions:** use for discovery, tests of orchestration, packaging and AUR publication. Standard Linux runners have limited documented disk; do not assume deleting preinstalled software yields a reliable full Chromium build environment. GitHub-hosted jobs have a six-hour limit. [Runner reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners), [execution limits](https://docs.github.com/en/actions/reference/limits)
- **Blacksmith OSS sponsorship:** one possible fit if ordinary free capacity is inadequate and the project qualifies. Its [Celery case study](https://www.blacksmith.sh/customer-stories/celery) describes donated compute and storage. Eligibility and grant size need confirmation; this plan does not assume them or send an application.
- **Donated/self-hosted runner:** supports the same workflows and can provide persistent build storage. It needs sufficient existing hardware and someone to maintain it; electricity and hardware are still costs. Keep this as a fallback, not an additional service in the initial design.
- **Depot:** explicitly acceptable to the user instead of Blacksmith. Its current [pricing](https://depot.dev/pricing) lists a $20/month Developer plan with included usage and a seven-day trial, plus usage caps. This does not establish a permanent free tier. Confirm any OSS arrangement and its compute/storage coverage before choosing it for recurring builds; otherwise present it as a paid option when asking the user.
- **GitHub larger runners:** plausible compile hosts, but a public repository does not make them free. Check [larger-runner billing](https://docs.github.com/en/billing/managing-billing-for-your-products/managing-billing-for-github-actions/about-billing-for-github-actions) before proposing them.
- **BuildJet:** exclude; it announced a March 2026 shutdown. [Shutdown announcement](https://buildjet.com/for-github-actions/blog/we-are-shutting-down)
- **Other OSS CI offers, including Cirrus:** potential follow-up only if needed. No unverified free allowance, disk allocation or sponsorship is part of the proposed system.

Start with cold builds and modest caches within confirmed free limits. Do not make paid sticky disks, remote build execution, a cache server or a huge uploaded build directory prerequisites. Incremental compilation is an optimization to add after the cold path works.

## 5. Release discovery, no-op detection and retries

Run discovery hourly, offset from the top of the hour, plus `workflow_dispatch`. GitHub schedules can be delayed; record and catch up missed releases. A `release` event in our repository does not subscribe to releases in Electron's repository. An upstream webhook is optional, not required infrastructure.

Use paginated official GitHub release data and verify source tags:

- Stable: all newly published non-prerelease Electron releases since an explicit activation baseline, including maintenance releases on older major lines.
- Prerelease: newly published alpha/beta releases, excluding nightly. Track the highest eligible prerelease version for the AUR package.
- Nightly: published releases from [`electron/nightlies`](https://github.com/electron/nightlies/releases); resolve their corresponding source tags in `electron/electron`.

The nightly distinction was verified: `v47.0.0-nightly.20261023` has assets in the nightlies repository, while its [Electron source tag](https://api.github.com/repos/electron/electron/git/ref/tags/v47.0.0-nightly.20261023) peels to commit `0e77ca92cd373a1b145c1e10d83189c137ea1b3e`. Do not build the artifact repository's default branch or today's moving `main` for yesterday's nightly.

On first activation, seed current channel targets and record the baseline; do not backfill Electron's entire history. Thereafter enumerate releases rather than relying solely on `/releases/latest`, which can miss intermediate and maintenance releases. Handle annotated tags by peeling them to commits.

Build every newly detected stable Electron release. If an older major gets a patch after a newer major has shipped, publish its successful artifact but do not downgrade any stable-Electron AUR package. Keep explicit statuses for releases whose compatibility patch set is unavailable. Queue all Electron prereleases by default; nightly may coalesce a backlog to the newest pending nightly, with skipped targets recorded. Do not coalesce away stable Electron releases.

Independently poll Discord's official distribution manifests for `stable`, `ptb` and `canary`, verifying each endpoint and payload format during implementation. Store channel-specific client/module versions, checksums and patch applicability. An Electron update fans out to the three Discord channels after deduplicated compilation. A Discord channel update fans out only to that channel's three Electron packages, reusing existing runtimes unless a compiled compatibility change is required.

### Separate the expensive and cheap identities

Compute canonical, content-based hashes:

```text
runtime_key = SHA256(
  upstream version and commit + all patches compiled into Electron
  + pinned build tools/toolchain/container
  + GN arguments + target architecture + build script contents
)

helper_key = SHA256(
  helper sources and dependency locks + target architecture and native ABI
  + helper compiler/toolchain and build arguments
)

integration_key = SHA256(
  runtime_key + helper_key + Electron channel + Discord channel
  + Discord core/module versions and checksums
  + module patches including the Krisp patcher and its pinned tool dependencies
  + launcher contents + package template contents
)

validation_key = SHA256(integration_key + test contents/configuration)
```

Exclude polling timestamps, unrelated documentation edits and the entire repository HEAD from these keys. A package recipe or standalone helper edit should not force a Chromium rebuild. If the helper requires Electron-specific headers rather than a stable Node-API interface, include their exact identity in its key. A validation change should rerun validation against an existing artifact. A changed patch compiled into Electron must rebuild it even when Electron's version is unchanged.

Discord channel names alone must not affect `runtime_key` or `helper_key`. Share identical artifacts across clients, while keeping validation results, installed-package revisions and AUR publication state separate for each `(electron_channel, discord_channel)` pair. Record a compiled compatibility variant through its actual patch inputs rather than an arbitrary channel label.

Before allocating compile-runner compute, reconcile these cases:

1. All keys already validated and AUR matches: exit successfully, without a compile job or empty Git commit.
2. Runtime exists and its manifest/checksum match, but integration changed: reuse it; rebuild only helper/integration components that changed, then package and test.
3. Validated release exists but AUR publication failed: retry AUR only.
4. Artifact exists but tests changed: run the new tests; do not recompile unnecessarily.
5. Runtime key is new: reserve capacity, assemble, build and validate.
6. Same key failed from a patch/compile incompatibility: retain diagnostics and wait for changed inputs or explicit retry. Retry transient download/runner errors a bounded number of times with backoff.

Use immutable versioned runtime assets and a full build manifest. Do not reuse an artifact solely because its filename resembles the requested version. If a source tag moves, flag it instead of silently replacing an existing release's bytes.

Persist state transitions such as `pending`, `building`, `failed`, `validated`, `released`, `aur-published`, with run IDs and timestamps. Reconcile abandoned jobs from GitHub run status. A concurrency lock prevents two writers; the durable state and release history prevent lost work. Never rely on the Actions pending-job queue as the only release backlog.

## 6. Compilation and validation

First build a pinned Discord fork revision without our ports to establish a reference. Then assemble the integration onto the selected current stable Electron source. Add prerelease and nightly only after the common build path works.

Follow Electron's [official build instructions](https://www.electronjs.org/docs/latest/development/build-instructions-gn) and [Linux prerequisites](https://www.electronjs.org/docs/latest/development/build-instructions-linux). Pin build-tools/depot_tools, use the target's DEPS/toolchain, and configure local compilation without requiring Google's remote execution credentials. Current instructions mention GN/Siso while retaining Ninja-compatible examples; use the backend supported by the pinned target.

The release distribution target is `electron:electron_dist_zip`. Build the real runtime, including its resources and notices. `electron-builder` packages applications and is not a substitute for compiling Electron. Avoid a component/development build as the distributed runtime.

Record effective GN arguments, dependency commits, compiler version, source/patch hashes, runtime version, Discord module hashes and artifact checksums. Pin the environment enough to reproduce inputs; do not claim byte-for-byte reproducibility until tested.

Required validation layers:

1. **Assembly:** patch application, expected linked binding and exported API, dependency patch ordering, version consistency.
2. **Runtime:** launch a small Electron fixture; check renderer installation of `createDiscordStream`; load the shipped native modules; exercise synthetic frame delivery, callbacks and teardown.
3. **Discord integration:** launch each channel's actual pinned client; verify resource/module resolution, call setup and native streaming behavior for each runtime/client combination. Record channel, client/module/runtime versions and results in a compatibility manifest. Stable passing does not establish PTB or Canary compatibility. Run package validation with matrix fail-fast disabled so a failing client does not cancel the others.
4. **Audio:** isolated PipeWire test session, controlled application streams and lifecycle checks described below.
5. **Krisp:** verify patched binary structure, module initialization and actual noise-suppressed microphone output for every channel/module/runtime combination being published. Check restart, call reconnection and toggling suppression; verify audio still works with suppression disabled. A patcher exit code or visible UI toggle alone is not proof of working suppression.
6. **Arch packaging:** build the binary package in a clean Arch environment, inspect dependencies and files, install it, and launch the packaged executable. Verify native binaries remain unstripped and Krisp's required assets/directories survive packaging.

A headless smoke test does not prove a real Wayland picker, hardware acceleration or remote stream works. During bootstrap and after relevant bridge changes, verify sending/joining/stopping streams on actual X11 and Wayland desktops. Turn reproducible regressions into local fixtures so ordinary updates can remain automatic. Do not embed a personal Discord token in public CI or describe a synthetic bridge test as an end-to-end Discord call test.

### Krisp implementation and release gate

Import the current package's patcher and dependencies (`python-pyelftools` and `python-capstone`), pin them, and run the patch against each channel's original `discord_krisp.node` in the packaging stage. Preserve its model/support files, `KMS/logs` initialization directory and `!strip` handling. Start from pristine locked inputs; retain input/output checksums and patcher provenance in the integration manifest.

Validate ELF architecture, required symbols, virtual-address/file-offset conversion and the expected instruction pattern before accepting a patch. Cover both fresh and already-patched inputs. An unsupported module must stop publication for that combination with useful diagnostics; never comment out the patcher and publish with broken Krisp. These are small validation additions around the existing implementation, not a replacement patching system.

Use a fixed speech-plus-noise microphone fixture and the same speech-only reference to verify effective suppression with understandable speech retained. Establish the expected behavior against the current working package, then check the custom runtime with Krisp enabled/disabled. Also test Krisp alongside a screen share so the per-app audio work does not interfere with microphone processing. Build a repeatable local module/client harness for CI, with real client verification during bootstrap and when the interface changes.

Reapply and revalidate when Discord changes Krisp or related voice-module payloads, including module-only updates. A patcher-only change invalidates integration/test results and package revisions, while reusing the existing Electron runtime. Keep the last working package if Krisp fails, and record which combination needs an updated upstream patch.

## 7. Per-application screenshare audio

### First determine whether the existing module can be configured correctly

Instrument only the relevant local module-wrapper calls in a development build. Capture source IDs, soundshare options, audio-enabled state and their lifecycle, without logging credentials or unrelated client state. Compare choosing a window versus a monitor, and audio enabled versus disabled.

Determine:

- Does the Linux client provide a usable application PID, a window ID, a portal stream identifier, or only an opaque token?
- Does the voice module already support application capture but receive the wrong options?
- Can its screen-share audio input be selected independently of the voice microphone?
- Which layer is actually opening the system monitor stream?

If the fix is setting existing source-specific options, ship a small, checksum-gated module-wrapper patch. Match a supported input hash, require exact match counts for edits and verify the resulting wrapper. Reject unknown layouts instead of applying a broad search-and-replace. That is simpler than adding a second capture implementation.

### Reuse venmic routing if a helper is necessary

[`Vencord/venmic`](https://github.com/Vencord/venmic) provides PipeWire routing/filtering and a native addon interface. Vesktop's [`src/main/venmic.ts`](https://github.com/Vencord/Vesktop/blob/main/src/main/venmic.ts) demonstrates selecting audio nodes and excluding unwanted streams. Its [`screenShareFixes.ts`](https://github.com/Vencord/Vesktop/blob/main/src/renderer/patches/screenShareFixes.ts) connects the resulting virtual source to a browser audio track.

Reuse the routing implementation or its native API behind our small adapter. Do not copy Vesktop's `getDisplayMedia` replacement into Discord: that would change the requested native path. Prefer an in-process addon or direct helper interface over introducing a local HTTP service. Preserve dependency license notices; venmic's MPL-2.0 and Vesktop's GPL licensing are distinct.

Vesktop's [picker implementation](https://github.com/Vencord/Vesktop/blob/main/src/renderer/components/ScreenSharePicker.tsx) also lets users choose audio sources. Its existence does not prove that Discord's selected video source can automatically be mapped to an audio process.

Proposed routing lifecycle:

1. Receive the selected source and audio toggle through the module boundary already used by Discord.
2. Resolve that source to the correct application/audio-node set.
3. Create a private PipeWire route containing only those playback streams.
4. Feed it into the native screen-share audio input, keeping the microphone and voice-call input separate.
5. Update routing when sources/nodes change; remove it on stop, switch, disconnect, crash and application exit.

The unresolved part is step 4: prove the voice module accepts the needed input/device or exposes a suitable native boundary. If capture is entirely private and cannot be redirected, changing public Electron alone may not fix it. Report the required additional hook explicitly; do not promise that an Electron-only patch is enough.

### Source identity and isolation

The [Wayland ScreenCast portal interface](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.ScreenCast.html) returns stream information, but does not guarantee a selected-window application PID. A PipeWire video node ID is not an audio node ID. X11 window metadata, browser subprocesses, Flatpak namespaces and games with helper processes also need careful mapping.

Prefer verified IDs and process relationships over titles. If identity cannot be resolved safely, report that app audio is unavailable for that selection; never silently substitute system audio. A separate audio selector is a last-resort design change, not an assumed requirement in this plan.

Test with two applications generating different tones: selecting A must transmit A and exclude B. Also test microphone independence, exclusion of received Discord audio, audio-disabled state, pause/resume, source switching, process restart and cleanup. Cover X11 and Wayland separately, including a multi-process application. Keep this feature separately switchable so an audio experiment cannot break the working native video bridge.

## 8. Nine AUR packages and automatic publication

AUR hosts package recipes; **GitHub Releases hosts the compiled binaries**. Generate all nine package recipes from the template derived from the pinned current AUR package, with explicit Electron and Discord channel configuration. Reuse the same runtime asset URL and checksum wherever the runtime key matches. Follow [AUR submission guidance](https://wiki.archlinux.org/title/AUR_submission_guidelines) and [PKGBUILD conventions](https://wiki.archlinux.org/title/PKGBUILD).

Each package installs into a directory unique to its Electron/Discord channel pair and exposes a distinct executable/desktop entry. Do not overwrite system Electron or official Discord installations. Give each pair a separate default user-data directory so all nine packages can coexist without mutating one another's state. Configure Discord's release-channel identity, resource/module roots and updater settings from that client's channel configuration; confirm Stable, PTB and Canary each launch their intended client.

The PKGBUILD uses fixed URLs and checksums for our runtime/helper archive and the official Discord core/module distributions. It packages these into the private installation layout, applies the existing resource/bootstrap and Krisp patches plus our narrow integration changes, and configures local module loading. Download proprietary client payloads from Discord rather than pretending they are part of the open-source runtime. Users run `makepkg` only to assemble and patch downloaded files as the current package does; C++ compilation already happened in CI. CI must test the exact same packaging/patching steps that users execute.

Do not execute Discord during `package()`, access a real user's home, use an unversioned `latest` URL, calculate `pkgver` by making a live API request, or use `SKIP` for downloaded binary checksums. Reproduce required bootstrap/resource settings from the working AUR research and test them against the pinned client.

Control local desktop/module updates through packaging so the tested combination remains installed. Poll each Discord channel's distribution manifest: a client/module-only update should reuse the runtime, validate compatibility and publish revisions only for the affected packages. Track `pkgrel` independently for all nine package names. The remotely served Discord UI can still change; document this limit and retain diagnostic version information.

### Versions

Use Electron's version as `pkgver`, normalized for Arch; use `pkgrel` for our revisions at the same Electron version. A changed patch/runtime, helper, client/module set or package recipe increments `pkgrel` when it changes the installed package. Reset `pkgrel` on a genuinely newer `pkgver`.

Verify normalization and ordering with Arch's `vercmp`, including alpha-to-beta transitions, numeric suffixes and nightly dates. Arch `vercmp`-validated examples are `46.0.0beta.1` and `47.0.0nightly.20261023`; retain ordering regression tests, including promotion to stable. Keep upstream version strings unchanged in provenance metadata.

Each stable-Electron package selects the highest stable semantic version validated with its own Discord channel. Maintenance releases for older majors still get artifacts but never move AUR backwards. Electron prerelease excludes nightly, and stable promotion does not relabel an old beta as a stable build. Keep each package on its last good version until a newer eligible combination passes. A broken Canary module must not block Stable or PTB publication, and a failed package must never advertise another channel's validation result.

### Publication transaction

For every successful new Electron/Discord channel combination, automatically, reusing an existing shared runtime release where appropriate:

1. Verify build identity and required tests; create a draft GitHub Release with versioned assets, checksums and `build-manifest.json`.
2. Generate the final PKGBUILD from these exact asset URLs and official payload checksums. Generate `.SRCINFO` with `makepkg --printsrcinfo` in Arch.
3. Validate/package against the draft assets through authenticated CI downloads or a local source cache. Check metadata, dependency resolution and installation.
4. Publish the release; verify the public asset downloads and checksums before updating AUR. Never replace published asset bytes.
5. Clone the relevant AUR repository over SSH, compare generated files, commit only a real change and push the fast-forward update.
6. Confirm the remote commit and record `aur-published` against the integration key.

The AUR publisher uses a dedicated maintained account/key with access to the nine package repositories. Store the key in Actions secrets, verify the AUR host key and expose credentials only to the publication job. Run source compilation without AUR credentials. Keep GitHub write permissions scoped to jobs that publish or update state; pin external actions.

Serialize publication per package. Recheck the current channel head before pushing so an older completed job cannot overwrite a newer package. On a non-fast-forward push, fetch and regenerate from the current authoritative template/state; do not blindly merge generated PKGBUILD text or force-push.

If GitHub publication succeeds and AUR fails, retry only AUR using the existing release. If an AUR push succeeds but state recording fails, inspect the remote files/commit and reconcile without a second version bump. Routine updates need no per-release manual approval after initial setup.

## 9. Implementation order and acceptance criteria

### Phase 1 — prove the two expensive assumptions

- [ ] Create the small project repository and pin a reference Discord fork/client/module combination.
- [ ] Import and pin the current AUR packaging baseline, launcher, Krisp patcher and relevant compatibility fixes.
- [x] Select compiler capacity: user-authorized local PC, after Blacksmith account suspension.
- [ ] Execute the bounded reference experiment and measure local resource use.
- [ ] Build the reference fork, then port the bridge to current stable Electron.
- [ ] Demonstrate a real Discord stream starts, can be joined and stops without the missing custom API failure.
- [ ] Demonstrate working Krisp initialization and effective microphone noise suppression with the current package baseline and custom runtime.
- [ ] Record native ABI behavior and actual compile resource usage.

Exit: one working custom runtime, one reproducible build manifest, and a measured free-CI feasibility decision. If these fail, resolve them before building a large release pipeline.

### Phase 2 — stable automation and packaging

- [ ] Implement discovery, canonical hashes, durable state and bounded retry behavior.
- [ ] Derive the shared package template and stable launcher/layout from the imported current package, retaining its resource/bootstrap and Krisp fixes.
- [ ] Implement compile/validate/release/AUR jobs with publication-only credentials.
- [ ] Register the package and configure its AUR access during deployment setup.
- [ ] Demonstrate a successful automatic AUR update from a new stable target.
- [ ] Repeat unchanged inputs and prove no compile job starts and no Git/AUR commit is created.
- [ ] Simulate a publish failure and prove recovery reuses the existing runtime.

### Phase 3 — full Electron and Discord channel matrix

- [ ] Add Electron prerelease/nightly and Discord PTB/Canary configuration, producing all nine packages through the same scripts/template.
- [ ] Verify the PTB and Canary manifests, channel identity, resource paths and module layouts; lock their inputs separately.
- [ ] Validate each channel's Krisp patch applicability and effective suppression on its supported Electron runtimes; reject broken combinations without blocking healthy ones.
- [ ] Demonstrate one shared runtime build feeding three Discord packages when compiled inputs match.
- [ ] Demonstrate a Canary-only client/module update reuses runtimes and updates only compatible Canary packages.
- [ ] Verify all nine packages have distinct installation paths, launchers, desktop entries and profiles.
- [ ] Validate actual nightly tag-to-source resolution and prerelease version ordering.
- [ ] Prove a broken Electron nightly or Discord channel cannot block unrelated package publication or downgrade any channel pair.
- [ ] Simulate multiple stable releases between polls and an older-major maintenance release.
- [ ] Confirm the measured monthly free capacity supports the desired workload, or pause dependent work and ask the user to choose sponsorship, donated compute, an approved paid option or another direction.

### Phase 4 — application audio

- [ ] Trace source selection/options and locate the screen-share audio input boundary.
- [ ] Try the smallest existing-option correction first.
- [ ] If necessary, implement the venmic-based helper and a minimal module adapter.
- [ ] Pass two-application isolation and lifecycle tests on X11 and Wayland for each supported Discord channel/module variant; validate the relevant runtime combinations.
- [ ] Release the fix through the same fingerprinted pipeline, without remote client UI changes if the proven boundary allows it.

### Phase 5 — maintenance hardening

- [ ] Exercise duplicate schedules, interrupted jobs, exhausted quota, expired caches, tag changes and AUR push races.
- [ ] Publish a compatibility matrix and documented last-good-version recovery procedure.
- [ ] Add selective caching only when measured savings justify it and storage remains free or sponsored.
- [ ] Remove patches absorbed by upstream and reuse Discord's newer ports as they appear.

## 10. Completion definition and remaining uncertainties

The project is complete when all nine packages, derived from the current AUR packaging baseline, install the intended official Discord Stable, PTB or Canary client with the corresponding stable, prerelease or nightly custom Electron runtime; new stable Electron releases are detected and attempted without being lost; each Discord channel's updates are independently detected and packaged; successful newer combinations automatically update their AUR packages; identical runtime inputs are compiled once and reused across clients; unchanged builds and publication retries do not trigger unnecessary compilation; Krisp noise suppression demonstrably works; and the per-app audio feature demonstrably excludes other applications for the supported combinations.

The remaining research questions are concrete: actual cold-build cost within a suitable provider's free allowance, private voice/Krisp-module compatibility across Electron majors and client channels, the real Linux source-selection arguments, and an independently selectable native screen-share audio input. Keep them visible as feasibility gates. Existing packaging patches provide the starting implementation, but do not by themselves prove compatibility with a newly compiled runtime. Neither unlimited free compilation nor permanently conflict-free Chromium ports has been established. If no suitable free CI can be established, the authorized next step is to pause dependent work and ask the user, not to incur charges.
