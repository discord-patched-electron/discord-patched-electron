"""CI channel switches, separate from historical snapshot formats."""

import argparse
import json

from common import DISCORD_CHANNELS, ROOT, enabled_electron_channels, read_json


def enabled_discord_channels() -> tuple[str, ...]:
    channels = read_json(ROOT / "config/channels.json")["discord_channels"]
    if (
        not channels
        or len(set(channels)) != len(channels)
        or any(channel not in DISCORD_CHANNELS for channel in channels)
    ):
        raise ValueError("Invalid enabled Discord channels")
    return tuple(channels)


def require_enabled_discord(channel: str):
    if channel not in enabled_discord_channels():
        raise ValueError(f"Disabled Discord channel: {channel}")


def select_discord_channels(selection: str = "all") -> tuple[str, ...]:
    channels = enabled_discord_channels() if selection == "all" else tuple(selection.split(","))
    if not channels or len(set(channels)) != len(channels):
        raise ValueError("Select distinct enabled Discord channels")
    for channel in channels:
        require_enabled_discord(channel)
    return channels


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("selection", nargs="?", default="all")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--electron", action="store_true", help="List enabled Electron tracks")
    args = parser.parse_args()
    if args.electron and args.selection != "all":
        parser.error("--electron lists all enabled Electron tracks")
    selected = (
        enabled_electron_channels() if args.electron else select_discord_channels(args.selection)
    )
    print(json.dumps(selected) if args.json else "\n".join(selected))
