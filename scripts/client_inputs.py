"""Read an exact client snapshot and select independent validation channels."""
from pathlib import Path

from common import DISCORD_CHANNELS, read_json
from generate_package import validate_client


def load_clients(path: Path, channels: list[str] | None = None) -> dict:
    snapshot = read_json(path)
    if snapshot.get("schema") != 1 or set(snapshot.get("clients", {})) != set(DISCORD_CHANNELS):
        raise ValueError("Expected a complete three-channel client lock")
    selected = list(DISCORD_CHANNELS) if channels is None else channels
    if not selected or len(set(selected)) != len(selected) or any(channel not in DISCORD_CHANNELS for channel in selected):
        raise ValueError("Select distinct supported Discord channels")
    return {channel: snapshot["clients"][channel] for channel in selected}


def validate_channel(channel: str, client: dict):
    if not isinstance(client, dict) or client.get("channel") != channel:
        raise ValueError("Client lock belongs to a different Discord channel")
    validate_client(client)
