"""Bundle the pinned component runtime locally; installed apps need no build tools."""

import argparse
import shutil
import subprocess
from pathlib import Path

from common import ROOT


def build(output: Path | None = None):
    output = output or ROOT / "out/ui"
    output.mkdir(parents=True, exist_ok=True)
    # Standalone makepkg recipes carry the bundle generated with their source
    # archive. Building an installed binary package never needs Bun or npm.
    prebuilt = ROOT / "prebuilt-ui"
    if prebuilt.is_dir():
        for name in ("config-app.js", "ui-runtime.cjs", "PREACT-LICENSE.txt"):
            shutil.copy2(prebuilt / name, output / name)
        return output
    for entry, name, target, format_name in (
        ("integration/updater/ui/config-app.jsx", "config-app.js", "browser", "iife"),
        ("integration/discord-ui/ui-runtime.cjs", "ui-runtime.cjs", "browser", "cjs"),
    ):
        subprocess.run(
            [
                "bun",
                "build",
                str(ROOT / entry),
                "--target=" + target,
                "--format=" + format_name,
                "--minify",
                "--outfile=" + str(output / name),
            ],
            cwd=ROOT,
            check=True,
        )
    shutil.copy2(ROOT / "node_modules/preact/LICENSE", output / "PREACT-LICENSE.txt")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    build(parser.parse_args().output)
