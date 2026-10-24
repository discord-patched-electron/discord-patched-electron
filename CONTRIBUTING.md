# Contributing

Bug reports, desktop testing and focused fixes are welcome. For a larger change,
open an issue first so we can agree on its scope.

## Set up

Use Linux with Git, a C/C++ compiler, [uv](https://docs.astral.sh/uv/getting-started/installation/),
[Bun](https://bun.sh/docs/installation) and Node.js 24. The Bun version is pinned
in `package.json`. Bun manages JavaScript dependencies and commands; Node is
still used by Electron's ASAR tools and the `node:test` suite. uv installs Python
and the pinned Python, shell and C formatting tools.

```sh
git clone https://github.com/discord-patched-electron/discord-patched-electron.git
cd discord-patched-electron
uv sync --locked
bun install --frozen-lockfile --ignore-scripts
bun run test
bun run lint
```

The normal test suite needs no Discord account or Electron compilation. Some
Arch-specific assertions are skipped when `vercmp` is unavailable. CI also checks
generated packages in an Arch container. Container integration tests need Docker
and are separate from this suite.

## Find your way around

| Path           | What belongs here                                                   |
| -------------- | ------------------------------------------------------------------- |
| `integration/` | Launcher and Linux audio, camera and desktop adapters               |
| `scripts/`     | Discovery, source assembly, packaging and verification              |
| `tests/`       | Unit tests and native/audio fixtures                                |
| `config/`      | Pinned sources, clients and update/build policies                   |
| `patches/`     | Imported Discord changes and focused Electron compatibility patches |
| `packaging/`   | Arch template, distro hooks and pinned upstream reference files     |
| `.github/`     | CI workflows and shared test steps                                  |
| `docs/`        | User guides; `docs/agent/` holds implementation history             |

Generated files go in `out/`. Compiler workspaces and cached downloads are not
source files; keep them out of commits.

## Make a change

Create a branch, keep the change focused and add a regression test when changing
behavior. Existing tests show how to use temporary app trees and audio fixtures.
For a documentation-only change, formatting and checking the instructions is
usually enough.

```sh
git switch -c fix/describe-the-change
bun run format
bun run test
bun run lint
```

You can run just `bun run test:python` or `bun run test:js` while iterating.
Ruff checks and formats Python; Oxlint and Oxfmt handle JavaScript and repository
text. ShellCheck, shfmt and clang-format cover first-party shell and native files.
Formatting doesn't rewrite checksum-pinned upstream snapshots or patch files.

For workflow changes, install Go and run the same actionlint version as CI:

```sh
go install github.com/rhysd/actionlint/cmd/actionlint@v1.7.12
uv run actionlint
```

Put Go's binary directory on `PATH` first. Review permission changes and test
dispatch inputs as well as syntax. Pull requests never run on the local compiler.

Use `uv add` / `uv add --dev` or `bun add --dev --exact` to change dependencies,
and commit the corresponding `uv.lock` or `bun.lock`. CI installs from those locks
and disables JavaScript install scripts. Don't add a second package-manager lock.

Don't edit `packaging/upstream/` or reformat `patches/`. Upstream updates need an
explicit import with new checksums. Keep compatibility patches small so source
assembly can identify when an upstream change needs review.

## Test an app build

Most launcher, module and packaging changes reuse an existing verified Electron
artifact. Install `brotli` and follow [release packaging](docs/agent/releases.md)
to assemble a local app or dispatch a package test. Always use fresh output
directories. The release workflow tests Stable, PTB and Canary in parallel.

Launch fixtures use a fresh profile and verify the login form without signing
in. The optional mod check uses Equicord's official installer, then removes it
and verifies the original ASAR before packaging. Public packages don't contain
Equicord or a custom mod installer.

For audio and camera changes, use the documented
[runtime checks](docs/agent/runtime-validation.md). Synthetic fixtures cover
routing and native APIs; they don't prove that a real call works. Record what
you tested and what still needs a person. Never save or upload physical webcam
images or video. Don't include account data, tokens or private conversations in
logs, and don't run unattended account logins, messages or calls.

Full Electron compilation is a separate maintainer task on the local runner.
See [CI setup](docs/agent/ci-setup.md) and [build caching](docs/agent/build-caching.md).
Reuse retained objects and don't restart a healthy build. Blacksmith is not used for compilation in the current project setup.

## Send a pull request

Explain the problem, the resulting behavior and the checks you ran. Mention
desktop/driver limitations where relevant. Add user-facing documentation when a
setting or installation step changes. CI checks formatting, tests, source
assembly, Arch packaging and isolated native/audio fixtures.

Release maintainers select an immutable client snapshot and a verified runtime
in **Release Linux packages**. Leave publishing off for a dry run. Publishing
requires all qualification jobs to pass; AUR publication additionally needs its
own configured credentials. See [release packaging](docs/agent/releases.md) for
the exact steps. Don't publish packages from a locally modified or modded app.
