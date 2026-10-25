"""Build installer-shaped fixtures with Electron's independent ASAR implementation."""

import json
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@lru_cache
def loader(mod="Vencord", *, target=None, code=None):
    target = (
        target
        or {
            "Vencord": "/tmp/Mod fixture/Vencord/dist/patcher.js",
            "Equicord": "/tmp/Mod fixture/Equicord/equicord.asar",
        }[mod]
    )
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source = root / "source"
        source.mkdir()
        (source / "package.json").write_text(json.dumps({"name": "discord", "main": "index.js"}))
        (source / "index.js").write_text(code or f"require({json.dumps(target)})")
        archive = root / "app.asar"
        subprocess.run(
            [str(ROOT / "node_modules/.bin/asar"), "pack", str(source), str(archive)], check=True
        )
        return archive.read_bytes()


def install(resources, mod="Vencord"):
    (resources / "app.asar").rename(resources / "_app.asar")
    data = loader(mod)
    (resources / "app.asar").write_bytes(data)
    return data


def uninstall(resources):
    (resources / "_app.asar").replace(resources / "app.asar")
