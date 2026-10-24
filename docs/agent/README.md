# Agent docs

These notes preserve implementation details and investigation history. They are
useful to contributors and coding agents. The [main README](../../README.md) covers
using the app. Older reports describe the code and inputs at the time they ran.
Only **Canary + alpha/beta Electron** is enabled. Canary has better compatibility
with newer Electron versions. [Channel switches](channels.md) retain the other
Discord and Electron options without enabling them. Public test releases include
pacman packages. I test them before approving them through a manual action.

## Start here

- [Handoff and local build paths](handoff.md)
- [Implementation history](status.md)
- [Release packaging](releases.md)
- [Update discovery and scheduling](discovery.md)
- [Original plan](plan.md)

## Building and packaging

- [CI and local runner setup](ci-setup.md)
- [Build caching](build-caching.md)
- [Source ports](source-ports.md)
- [Arch packaging](packaging.md)
- [Build capacity investigation](build-capacity.md)

## Linux integration

- [Wayland audio implementation](wayland-audio.md)
- [Audio investigation](audio-investigation.md)
- [Camera startup](camera-startup.md)
- [Launcher, codecs and shortcuts](linux-launcher.md)
- [Previous Equicord loader](equicord.md) (historical)
- [Native runtime](linux-runtime.md)
- [Runtime validation](runtime-validation.md)
- [Test quality review](test-quality.md)
- [Saved test evidence](../evidence/)
