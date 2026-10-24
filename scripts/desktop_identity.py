"""Portal identity is an integration input, never a native compilation input."""


def desktop_id(discord: str) -> str:
    suffix = {'stable': 'Discord', 'ptb': 'DiscordPTB', 'canary': 'DiscordCanary'}[discord]
    return 'io.github.discord_patched_electron.' + suffix
