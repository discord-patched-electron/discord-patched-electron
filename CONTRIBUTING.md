# Contributing

Bug reports, desktop testing and focused fixes are welcome. Open an issue before
starting a larger change so we can agree on its scope.

## Set up

Install Git, [Bun](https://bun.sh/docs/installation),
[uv](https://docs.astral.sh/uv/getting-started/installation/) and Node.js 24 on Linux.
Native tests also need a C/C++ compiler. uv manages Python and the development
tools; Bun manages JavaScript dependencies. Node is used by Electron's ASAR tools
and the JavaScript test suite.

```sh
git clone https://github.com/discord-patched-electron/discord-patched-electron.git
cd discord-patched-electron
uv sync --locked
bun install --frozen-lockfile --ignore-scripts
```

## Make a change

Create a branch and keep the change focused. Add a regression test when changing
behavior, and update the relevant guide when a setting or installation step changes.

```sh
git switch -c fix/describe-the-change
bun run format
bun run lint
bun run test
```

`format` fixes style; `lint` checks it and reports code and workflow problems.
Ruff handles Python, and Oxlint/Oxfmt handle JavaScript and repository text.
The same commands also check shell scripts, native code and GitHub workflows.
Everything is installed by the setup commands above.

Use `bun run test:python` or `bun run test:js` to run one suite while iterating.
These tests need no Discord account or Electron build. Arch-specific assertions
are skipped when `vercmp` is unavailable; CI also checks generated Arch packages.
For documentation-only changes, run formatting and linting and check the links
and instructions you changed.

## Repository layout

| Path           | Contents                                               |
| -------------- | ------------------------------------------------------ |
| `integration/` | Launcher and Linux audio, camera and desktop adapters  |
| `scripts/`     | Discovery, source assembly, packaging and verification |
| `tests/`       | Unit tests and native/audio fixtures                   |
| `config/`      | Pinned sources, clients and build policies             |
| `patches/`     | Discord's Electron changes and compatibility patches   |
| `packaging/`   | Package templates and upstream reference files         |
| `.github/`     | CI workflows                                           |
| `docs/`        | User guides; implementation notes in `docs/agent/`     |

Generated files belong in `out/`. Don't commit downloaded clients, compiler
workspaces or test profiles. Don't edit `packaging/upstream/` or reformat
`patches/`: they contain pinned upstream files. Source updates need an explicit
import with new checksums.

## Dependencies

Use `uv add` or `uv add --dev` for Python dependencies and
`bun add --dev --exact` for JavaScript tools. Commit the corresponding `uv.lock`
or `bun.lock` with the change. Keep tool versions pinned so local checks and CI agree.

## Desktop and packaging changes

CI runs package, native-module and isolated audio checks. For local integration
tests, follow [runtime validation](docs/agent/runtime-validation.md). Container
tests need Docker. Most app changes can reuse a verified Electron runtime; see
[release packaging](docs/agent/releases.md) for local builds and CI releases.

Use fresh profiles for launch checks. Don't run unattended account logins,
messages or calls. Never save or upload physical webcam images or video, and
keep account data and private conversations out of logs. Synthetic tests don't
prove that a live call works; record any behavior you couldn't verify.

## Send a pull request

Explain the problem, the resulting behavior and the checks you ran. For desktop
changes, include your distribution, desktop environment and Wayland/X11 session.
Mention any remaining limitations.

Keep user guides focused on using the app. Build infrastructure, investigation
notes and test evidence belong in [agent docs](docs/agent/README.md).
