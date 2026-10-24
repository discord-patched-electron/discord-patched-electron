"""Active Discord targets, separate from historical snapshot formats."""

from common import DISCORD_CHANNELS, ROOT, read_json


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
