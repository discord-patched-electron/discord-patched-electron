"""Use Discord's channel identity for desktop integration and user settings."""


def desktop_id(discord: str) -> str:
    return {"stable": "discord", "ptb": "discord-ptb", "canary": "discord-canary"}[discord]


def profile_name(discord: str) -> str:
    return {"stable": "discord", "ptb": "discordptb", "canary": "discordcanary"}[discord]


def display_name(discord: str) -> str:
    return {"stable": "Discord", "ptb": "Discord PTB", "canary": "Discord Canary"}[discord]


def arch_conflicts(discord: str) -> list[str]:
    identity = desktop_id(discord)
    return [identity, "discord-canary-electron-bin"] if discord == "canary" else [identity]
