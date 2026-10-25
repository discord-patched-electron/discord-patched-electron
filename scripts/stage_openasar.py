"""Stage the pinned MIT Monaco editor used by OpenAsar's unchanged settings UI."""

import tarfile
import tempfile
from pathlib import Path

from common import ROOT, download, read_json, sha256


def stage(output: Path, source_archive: Path | None = None):
    lock = read_json(ROOT / "integration/updater/openasar/source.json")
    with tempfile.TemporaryDirectory(prefix="openasar-cdn-") as temporary:
        archive = source_archive or download(
            lock["archive"]["url"], Path(temporary) / "cdn.tar.gz", lock["archive"]["sha256"]
        )
        if sha256(archive) != lock["archive"]["sha256"]:
            raise ValueError("OpenAsar editor archive checksum mismatch")
        prefix = "cdn-" + lock["commit"] + "/"
        with tarfile.open(archive) as source:
            for member in source:
                relative = member.name.removeprefix(prefix)
                if not member.isfile() or not (
                    relative.startswith("monaco-editor/min/")
                    or relative in ("monaco-editor/LICENSE", "monaco-editor/ThirdPartyNotices.txt")
                ):
                    continue
                if ".." in Path(relative).parts or Path(relative).is_absolute():
                    raise ValueError("Unsafe OpenAsar editor path")
                destination = output / "bootstrap/cdn" / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(source.extractfile(member).read())
