"""Independent reproduction: a valid internal relative link is newly rejected."""

import io
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "integration/updater"))
import client

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as bundle:
        entry = tarfile.TarInfo("files/value.js")
        entry.size = len(b"internal fixture")
        bundle.addfile(entry, io.BytesIO(b"internal fixture"))
        entry = tarfile.TarInfo("files/deep/nested")
        entry.type = tarfile.DIRTYPE
        bundle.addfile(entry)
        for name, target in (("alias", "deep/nested"), ("entry.js", "alias/../../value.js")):
            entry = tarfile.TarInfo("files/" + name)
            entry.type, entry.linkname = tarfile.SYMTYPE, target
            bundle.addfile(entry)
    compressed = root / "module.br"
    compressed.write_bytes(subprocess.check_output(["brotli", "--stdout"], input=raw.getvalue()))
    destination = root / "files"
    # The candidate rejects this before creating entry.js. Baseline accepts it.
    try:
        client.extract_module(compressed, destination)
    except ValueError as error:
        assert "archive link" in str(error), str(error)
        print("CANDIDATE REJECTED valid target alias:", error)
    else:
        print("BASELINE ACCEPTED valid target alias")
    if not (destination / "entry.js").is_symlink():
        (destination / "entry.js").symlink_to("alias/../../value.js")
    assert (destination / "entry.js").read_bytes() == b"internal fixture"
    assert (destination / "entry.js").resolve().is_relative_to(destination)
    destination.rename(root / "published-module")
    published = root / "published-module"
    assert (published / "entry.js").read_bytes() == b"internal fixture"
    assert (published / "entry.js").resolve().is_relative_to(published)
    print("CONTROL PASSED: actual link stays internal and readable after relocation")
