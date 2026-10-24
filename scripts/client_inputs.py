"""Read an exact client snapshot and select independent validation channels."""

from pathlib import Path

from channels import enabled_discord_channels
from common import DISCORD_CHANNELS, read_json
from generate_package import validate_client


def load_clients(path: Path, channels: list[str] | None = None) -> dict:
    snapshot = read_json(path)
    if (
        snapshot.get("schema") != 1
        or not set(enabled_discord_channels()).issubset(snapshot.get("clients", {}))
        or not set(snapshot.get("clients", {})).issubset(DISCORD_CHANNELS)
    ):
        raise ValueError("Client lock must contain every enabled Discord channel")
    selected = list(enabled_discord_channels()) if channels is None else channels
    if (
        not selected
        or len(set(selected)) != len(selected)
        or any(channel not in enabled_discord_channels() for channel in selected)
    ):
        raise ValueError("Select distinct supported Discord channels")
    return {channel: snapshot["clients"][channel] for channel in selected}


def validate_channel(channel: str, client: dict):
    if not isinstance(client, dict) or client.get("channel") != channel:
        raise ValueError("Client lock belongs to a different Discord channel")
    validate_client(client)
