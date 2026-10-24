"""Check (or format) our shell and C/C++ files using uv's pinned tools."""

import argparse
import subprocess

from common import ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fix", action="store_true")
    args = parser.parse_args()
    # Limit traversal to source directories; never walk retained compiler trees.
    roots = [ROOT / name for name in ("scripts", "integration", "tests", "ci", "packaging/linux")]
    files = sorted(path for root in roots for path in root.rglob("*") if path.is_file())
    shells = [str(path) for path in files if path.suffix == ".sh"]
    native = [str(path) for path in files if path.suffix in {".c", ".cc", ".h"}]
    commands = [
        ["shfmt", "-i", "2", "-ci", "-w" if args.fix else "-d", *shells],
        ["shellcheck", "--external-sources", *shells],
        ["clang-format", *(["-i"] if args.fix else ["--dry-run", "--Werror"]), *native],
    ]
    for command in commands:
        subprocess.run(command, cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
