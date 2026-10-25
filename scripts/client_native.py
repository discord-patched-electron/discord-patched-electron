"""Apply validated native and voice adapters to a newly downloaded private module."""

import argparse
from pathlib import Path

from camera_scope import write_scope
from common import ROOT, write_json
from patch_app_audio import patch as patch_audio
from patch_krisp import patch as patch_krisp


def prepare(directory: Path, name: str):
    if name == "discord_voice":
        write_json(
            directory / "dpe-audio-patch.json",
            patch_audio(directory, ROOT / "integration/audio/app-audio.cjs"),
        )
        write_scope(directory / "discord_voice.node", directory)
    elif name == "discord_krisp":
        write_json(
            directory / "dpe-krisp-patch.json", patch_krisp(directory / "discord_krisp.node")
        )
        (directory / "KMS/logs").mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("name", choices=("discord_voice", "discord_krisp"))
    args = parser.parse_args()
    prepare(args.directory.resolve(), args.name)
