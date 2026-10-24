# Working on this repository

Start with [CONTRIBUTING.md](CONTRIBUTING.md). Implementation context, build
handoffs and test results are indexed in [docs/agent/README.md](docs/agent/README.md).
Keep user docs short; put investigation notes in that directory.

- Use `uv run` for Python. Run `bun run test` and `bun run lint` before finishing.
- Preserve pinned upstream files and patches; don't format them.
- Reuse verified runtimes for packaging. New CI compilations use the budgeted
  Blacksmith workflow. Don't restart a healthy compiler or discard retained local objects.
- Test Discord with fresh profiles. No unattended account login, messages or calls.
- Never save or upload webcam images/video, or screenshot the desktop during
  physical camera tests.
- Treat old agent notes as historical evidence; consult current code and locks
  before making changes. Don't restart old monitors or hibernate the PC without
  a current instruction to do so.
