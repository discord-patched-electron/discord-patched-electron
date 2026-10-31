"""Independent reproduction: target aliases defeat lexical relocation checks.

Run from checkout root using uv run. Passing assertions demonstrate the defect,
not the desired behavior. All files are synthetic; no downloaded code executes.
"""

import io
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "integration/updater"))
import client


def archive(root, link):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as bundle:
        entry = tarfile.TarInfo("files/value.js")
        entry.size = len(b"verified fixture")
        bundle.addfile(entry, io.BytesIO(b"verified fixture"))
        entry = tarfile.TarInfo("files/alias")
        entry.type, entry.linkname = tarfile.SYMTYPE, "."
        bundle.addfile(entry)
        entry = tarfile.TarInfo("files/entry.js")
        entry.type, entry.linkname = tarfile.SYMTYPE, link
        bundle.addfile(entry)
    path = root / "module.br"
    path.write_bytes(subprocess.check_output(["brotli", "--stdout"], input=raw.getvalue()))
    return path


with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    source = root / "files"
    client.extract_module(archive(root, "alias/../files/value.js"), source)
    assert (source / "entry.js").read_bytes() == b"verified fixture"
    published = root / "published-module"
    source.rename(published)
    assert not (published / "entry.js").exists()
    source.mkdir()
    (source / "value.js").write_bytes(b"unrelated synthetic sibling")
    assert (published / "entry.js").read_bytes() == b"unrelated synthetic sibling"
    assert not (published / "entry.js").resolve().is_relative_to(published)
    print(
        json.dumps(
            {"accepted": True, "after_rename": "dangling without sibling; wrong bytes with sibling"}
        )
    )
