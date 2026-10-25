"""Bundle the updater's two Python libraries from checksum-pinned wheels."""

import shutil
import tempfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from common import ROOT, download, sha256


def sources() -> dict:
    packages = tomllib.loads((ROOT / "uv.lock").read_text())["package"]
    result = {}
    for name, suffix in (
        ("capstone", "manylinux_2_17_x86_64.manylinux2014_x86_64.whl"),
        ("pyelftools", "py3-none-any.whl"),
    ):
        package = next(item for item in packages if item["name"] == name)
        wheel = next(item for item in package["wheels"] if item["url"].endswith(suffix))
        result[name] = {"url": wheel["url"], "sha256": wheel["hash"].removeprefix("sha256:")}
    return result


def stage(output: Path, archives: Path | None = None):
    with tempfile.TemporaryDirectory(prefix="updater-python-") as temporary:
        for name, source in sources().items():
            filename = Path(urlparse(source["url"]).path).name
            archive = (
                archives / filename
                if archives
                else download(source["url"], Path(temporary) / filename, source["sha256"])
            )
            if sha256(archive) != source["sha256"]:
                raise ValueError(f"Updater Python wheel checksum mismatch: {name}")
            module = "elftools" if name == "pyelftools" else name
            with zipfile.ZipFile(archive) as wheel:
                for member in wheel.infolist():
                    path = PurePosixPath(member.filename)
                    if path.is_absolute() or ".." in path.parts or not path.parts:
                        raise ValueError("Unsafe updater Python wheel entry")
                    if path.parts[0] != module and not path.parts[0].endswith(".dist-info"):
                        continue
                    if member.is_dir():
                        continue
                    target = output / "python" / str(path)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with wheel.open(member) as data, target.open("xb") as destination:
                        shutil.copyfileobj(data, destination)
                    target.chmod(0o644)
