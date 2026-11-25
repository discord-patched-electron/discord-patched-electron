"""Desktop identity for the error dialog before Electron is ready."""

import os
from pathlib import Path


def desktop_id() -> str:
    return (os.environ.get("CHROME_DESKTOP") or "discord-canary.desktop").removesuffix(".desktop")


def icon_path() -> Path:
    return (
        Path(os.environ.get("DPE_APP_ROOT") or Path(__file__).resolve().parents[2]) / "discord.png"
    )


def zenity_command(*arguments: str) -> list[str]:
    # GTK uses argv[0] as its program name / Wayland app_id. Unlike --name and
    # --class, this works with both GTK3 and GTK4 Zenity. Execute "zenity" while
    # retaining our identity as argv[0]. GTK4 accepts but ignores --window-icon;
    # Wayland resolves the icon from the matching installed desktop entry.
    return [desktop_id(), "--window-icon=" + str(icon_path()), *arguments]
