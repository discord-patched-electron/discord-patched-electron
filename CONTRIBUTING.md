# Contributing

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), Node.js 24
and a C compiler, then run:

```sh
uv sync --locked
npm ci --ignore-scripts
npm test
npm run lint
```

uv installs Python and the pinned Python, shell and C formatting tools.
Run `npm run format` before sending a change. CI also checks GitHub workflows with
[actionlint](https://github.com/rhysd/actionlint).

Keep changes small and add a regression test when fixing behavior. Don't edit
`packaging/upstream/` or reformat patches: these are checksum-pinned upstream files.
Use `uv add` or `npm install --save-dev --save-exact` to change dependencies,
and commit the lockfile.

Most changes need no Electron build. For an app build, install `brotli`, use a
verified runtime and follow [release packaging](docs/agent/releases.md).
Audio integration checks use disposable Docker containers. Full compiler setup
is in [CI setup](docs/agent/ci-setup.md); use the local runner for Electron builds.

The repository keeps implementation history in [agent docs](docs/agent/README.md).
Include the checks you ran and any limitations in your pull request.
