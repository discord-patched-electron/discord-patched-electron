"""Recognize official Vencord/Equicord loaders without executing installed code."""

import json
import re
import struct
from dataclasses import dataclass
from pathlib import Path

# Both official installers write a small ASAR with package.json and index.js.
# Read only that layout; a stale _app.asar alone does not mean a mod is installed.
MAX_LOADER_SIZE = 128 * 1024


@dataclass(frozen=True)
class ClientMod:
    name: str
    target: str
    loader: bytes


def detect(resources: Path) -> ClientMod | None:
    archive = resources / "app.asar"
    if archive.is_symlink() or not archive.is_file():
        return None
    try:
        with archive.open("rb") as stream:
            data = stream.read(MAX_LOADER_SIZE + 1)
        if not 16 <= len(data) <= MAX_LOADER_SIZE:
            return None
        size, header_size, payload_size, json_size = struct.unpack_from("<4I", data)
        if (
            size != 4
            or header_size != payload_size + 4
            or header_size % 4
            or not 0 < json_size <= payload_size - 4
            or 8 + header_size > len(data)
        ):
            return None
        files = json.loads(data[16 : 16 + json_size])["files"]
        if set(files) != {"index.js", "package.json"}:
            return None

        def read(name):
            entry = files[name]
            if "link" in entry or entry.get("unpacked"):
                raise ValueError("External loader member")
            offset, length = int(entry["offset"]), entry["size"]
            start = 8 + header_size + offset
            if type(length) is not int or offset < 0 or length < 0 or start + length > len(data):
                raise ValueError("Invalid loader member")
            return data[start : start + length].decode("utf-8")

        package = json.loads(read("package.json"))
        if package.get("name") != "discord" or package.get("main") != "index.js":
            return None
        match = re.fullmatch(r'\s*require\(("(?:[^"\\]|\\.)*")\);?\s*', read("index.js"))
        if not match:
            return None
        target = json.loads(match[1])
        path = Path(target)
        if not path.is_absolute() or "\x00" in target:
            return None
        if path.name == "patcher.js":
            name = "Vencord"
        elif path.suffix == ".asar":
            # EQUICORD_DIRECTORY also permits a custom desktop.asar filename.
            name = "Equicord"
        else:
            return None
        return ClientMod(name, target, data)
    except (FileNotFoundError, ValueError, KeyError, TypeError, AttributeError):
        return None
