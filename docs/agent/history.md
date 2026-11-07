# Investigation index

Dated reports and retained evidence. Check current code and locks before reusing
commands, versions or results. Start with the [maintainer guide](README.md) for
current workflows.

## Start here

- [KMS maintenance after one setup approval](kms-silent-maintenance-20261107.md).
- [Automatic KMS helper maintenance and executable grants](kms-updater-binding-20261107.md).
- [Native12 resource comparison, Native13 live capture and limits](capture-native12-comparison-20261107.md).
- [Live preview repair, Rust migration and stage profiling](capture-rust-profiling-20261106.md).

- [Managed latest capture build and launch validation](dev-capture-latest-20261106.md).

- [Current direct capture versus PipeWire and remaining gaps](current-pipewire-comparison-20261105.md).

- [Game capture pacing, delivered FPS and remaining CPU](game-capture-pacing-20261105.md).

- [VAAPI encoder import reuse and measurements](encoder-import-reuse-20261105.md).

- [PipeWire-free game and KMS capture](direct-gpu-capture-20261105.md).

- [Optimized capture resource and throughput benchmarks](capture-efficiency-20261105.md).

- [Vkcapture cursor feasibility, protocol implementation and validation](vkcapture-cursor-20261105.md).

- [Vkcapture automatic application audio](vkcapture-audio-20261105.md).

- [Codec exclusions and camera/monitor hotplug checks](codec-exclusions-hotplug-20261105.md).
- [Bug report ZIP export and validation](log-export-20261105.md).
- [Subsystem diagnostic coverage audit](diagnostic-coverage-20261105.md).
- [Repository skills](../../AGENTS.md#repository-skills): focused implementation
  and validation workflows under `agents/skills/`.
- [Complete screensharing review and regression fixes](screenshare-review-20261105.md).
- [Handoff](handoff.md): current configuration, retained builds and open test gaps.
- [Release packaging](releases.md): make, test and approve a release.
- [Runtime validation](runtime-validation.md): test native video and Krisp.
- [Channel switches](channels.md): enable or disable supported build tracks.

## Builds and updates

- [Local development guide](../development.md) and
  [manager implementation and evidence](dev-environments.md)

- [Required whole-diff upstream update review](upstream-update-review.md)

- [Complete Canary 1.0.2444 compatibility](client-2444-compatibility.md)

- [Discovery relaunch failure and CSP investigation](discovery-relaunch-20261105.md)

- [AppImage host library boundary and validation](appimage-host-libraries-20261105.md)

- [CI runners and budget](ci-setup.md)
- [CPU target and build caching](build-caching.md)
- [Source ports](source-ports.md)
- [Update discovery and scheduling](discovery.md)
- [Electron scheduling, retry and provider-usage reliability audit](electron-update-reliability-20261101.md)
- [Client and Electron updater](client-updater.md)
- [Vencord and Equicord preservation](client-mods.md)
- [Installer resilience and GitHub API limits](mod-network-resilience-20261031.md)

## Linux integration

- [Automatic AV1 cropping in the native software decoder](software-av1-crop-20261105.md)

- [Native AMD AV1 two-row padding and the 1082 fast path](native-av1-two-row-padding-20261105.md)

- [Content-only desktop media controls](media-controls-content-20261105.md)
- [Default-off desktop media controls](desktop-media-controls-20261105.md)

- [Automatic AV1 source-rectangle cropping and compatibility checks](automatic-av1-crop-20261105.md)

- [Niri scene-change judder: packet pacing fix and viewer measurements](niri-transition-timing-20261104.md)

- [Receiver presentation clock and native-size AMD AV1 padding](receiver-clock-av1-padding-20261104.md)

- [Live source budgeting and receive decoder diagnostics](source-bitrate-decode-20261104.md)

- [Optimized picker closure and native handoff](optimized-handoff-20261104.md)

- [Receiver pixel requests and motion bitrate](bitrate-headroom-20261104.md)

- [Direct capture from restricted Electron processes](capture-host-permission-20261104.md)

- [Mentions-only desktop badges](badge-mentions-20261102.md)
- [Webcam flip and matching preview](webcam-flip-20261101.md)
- [Device hotplug PulseAudio abort](hotplug-volume-abort-20261102.md)
- [Native patch-chain compatibility after the hotplug guard](native-patch-chain-20261103.md)
- [Current codec roundtrip audit](codec-audit-current.md)
- [GPU HEVC, unpadded AV1 resampling and capture chroma correction](hevc-av1-chroma-20261104.md)
- [Windows encoder configuration, quality experiments and remaining parity gaps](windows-encoder-parity-20261104.md)
- [HEVC hardware support and the missing Linux encoder backend](hevc-native-gap-20261103.md)
- [Native VP9 availability on Linux and Windows](vp9-native-availability-20261103.md)
- [Presentation assets at client startup](startup-presentation-20261102.md)

- [Video/screenshare popout floating and geometry](video-popout-20261101.md)
- [Quality-scaler diagnosis and AMD encoder efficiency](quality-scaler-20261103.md)
- [AMD H.264 quantizer contract and allocation-aware adaptation](encoder-allocation-20261103.md)
- [H.264 bitrate cap regression after encoder reconfiguration](bitrate-cap-regression-20261103.md)
- [Higher screenshare bitrate request experiment](screenshare-bitrate-experiment-20261103.md)
- [Fresh-profile H.264 viewer library-path failure](openh264-viewer-path-20261103.md)
- [Screenshare bitrate degradation and capture backend research](screenshare-performance-20261101.md)
- [Capture methods, GPU handoff and implementation priorities](capture-methods-20261102.md)
- [Capture implementation and isolated validation](capture-implementation-20261102.md)
- [Capture stop fixes and complete-pipeline benchmarks](capture-pipeline-20261102.md)
- [Capture efficiency execution brief and acceptance criteria](capture-efficiency-plan-20261102.md)
- [Capture efficiency implementation, repeated results and limits](capture-efficiency-results-20261102.md)
- [Matched capture benchmark workflow](capture-benchmark-workflow-20261102.md)
- [Native capture fresh-frame delivery and scoped ABI](capture-delivery-20261102.md)
- [KMS monitor capture and final integration checks](kms-monitor-20261102.md)
- [Optimized sources in Discord’s native picker](optimized-picker-20261102.md)
- [Picker previews, native card styling and capture compatibility](picker-refinements-20261102.md)
- [Persistent picker previews, empty state and native cog settings action](picker-persistent-previews-20261102.md)
- [Stock OBS vkcapture compatibility and stable game launcher](game-compatibility-20261102.md)
- [PEAK and Steam runtime socket visibility](peak-steam-runtime-20261102.md)
- [GPU cursor composition for game capture](game-cursor-20261102.md)
- [Portable monitor ordering](portable-monitor-order-20261103.md)
- [Optimized source loss and monitor audio](source-loss-20261102.md)
- [DPE-scoped direct capture permission](kms-scoped-permission-20261102.md)
- [Game relay NVIDIA/Intel portability and fallback boundaries](capture-relay-portability-20261102.md)
- [Selectable capture backends, source chooser and native handoff](capture-backends-20261102.md)
- [Direct Wayland producer and GPU buffer ownership](direct-wayland-backend-20261102.md)
- [Opt-in Vulkan/OpenGL game capture and packaged hooks](vkcapture-backend-20261102.md)
- [GL semaphore visibility and EGL/GLX completion bridge](vkcapture-gl-semaphore-20261102.md)
- [Native GPU dimensions and Mutter synchronization](screenshare-native-gpu-20261102.md)
- [Client GPU synchronization correction and delayed-fence validation](screenshare-sync-20261102.md)
- [Codec negotiation and incoming video](screenshare-codecs-viewing-20261102.md)
- [NVIDIA NVENC compatibility source review](screenshare-nvidia-20261102.md)
- [Intel encoding compatibility source review](screenshare-intel-20261102.md)
- [Actual Minecraft high-FPS capture checks](screenshare-minecraft-20261102.md)
- [Installed capture permission, confirmation and disabled commands](settings-permission-20261102.md)
- [Screensharing settings and live diagnostics](screenshare-settings-20261102.md)
- [Desktop controls, scrolling and primary-selection paste](desktop-preferences-20261101.md)
- [Settings descriptions, titlebar behavior and autoscroll dependency](settings-refinements-20261101.md)
- [Native middle-click patch, regression checks and runtime artifact](middle-click-native-20261101.md)
- [Tray icons and bilingual spell checking](tray-spellcheck-20261101.md)
- [Spelling diagnosis and Languages shortcut](spelling-menu-20261101.md)
- [Native spelling checks for prefilled message editors](spelling-edit-20261101.md)
- [Dictionary filters, removal, progress and tray reset state](dictionary-controls-20261101.md)
- [Vencord/Equicord tray presets and asset credits](tray-presets-20261101.md)
- [Individual tray preset links and essential-flags copy](tray-row-presets-20261101.md)
- [Collapsed window controls and Custom tray preset state](controls-custom-20261101.md)
- [In-window audio picker and detection research](audio-picker.md)
- [Cross-desktop audio detection](audio-auto-detection.md)
- [Wayland application audio](wayland-audio.md)
- [Audio investigation and mixer fix](audio-investigation.md)
- [Camera startup](camera-startup.md)
- [Launcher, codecs and shortcuts](linux-launcher.md)
- [OpenAsar playback crash and flag audit](video-playback.md)
- [Editable presets, recovery and initial GPU flag research](launch-presets.md)
- [Deeper Linux GPU flag audit, dependencies and codec tests](linux-gpu-flags.md)
- [Shared Discord UI runtime and adapters](discord-ui.md)
- [OpenAsar settings entry and diagnostic pages](openasar-settings.md)
- [Blank voice debug popouts and opener compatibility](debug-popout.md)
- [Compact Flags tab, file storage and automated codec checks](flags-file.md)
- [Software codec initialization and sandbox fix](software-codecs.md)
- [Codec playback verification and NVIDIA HEVC flags](codec-support.md)
- [External links, test-profile isolation and Zen verification](external-links.md)
- [Canary window identity and runtime reuse after updates](desktop-identity.md)
- [Local runtime checks](linux-runtime.md)

- [Discord-style settings, setup and working-tree checks](discord-native-ui-20261031.md)

## History and evidence

- [Branch consolidation](branch-consolidation-20261031.md): final integration of
  the audio picker, bootstrap recovery and reliability review worktrees.

These pages explain past decisions and preserve test results. They are not
current setup instructions or authorization to repeat earlier actions.

- [Offline reliability and safety review, 30–31 October](reliability-review-20261030.md)
- [Reliability fixes and regression checks, 31 October](reliability-fixes-20261031.md)
- [Ongoing project-wide reliability work](project-reliability-20261031.md)
- [Reliability delivery audit and final validation](reliability-delivery-audit-20261031.md)
- [Build and release history](handoff-history.md)
- [Updater implementation history](updater-history.md)
- [Early implementation milestones](status.md)
- [Original plan](plan.md)
- [Initial build capacity experiment](build-capacity.md)
- [Older Arch packaging path](packaging.md)
- [Previous Equicord loader](equicord.md)
- [Proprietary payload cleanup](proprietary-payload-cleanup.md)
- [Saved test reports](../evidence/)

- [Blacksmith build economics and dictionary feedback](blacksmith-build-economics-20261101.md)

- [Free build capacity and incremental checkpoints](free-build-reuse-20261101.md)

- [AppImage review and package repository UX](packaging-ux-20261101.md)
