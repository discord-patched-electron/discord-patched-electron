"""Run the repository's pinned ASAR CLI with its available Bun runtime."""

from pathlib import Path

from common import ROOT


def command(executable: str, *arguments: str) -> list[str]:
    # Only the pinned JS entry point is interpreted by Bun. Preserve explicitly
    # supplied external tools, which may be executables or custom wrappers.
    pinned = ROOT / "node_modules/.bin/asar"
    prefix = ["bun", "--bun"] if Path(executable).resolve() == pinned.resolve() else []
    return [*prefix, executable, *arguments]
