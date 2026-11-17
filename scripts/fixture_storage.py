"""Own disposable fixture payloads while keeping reports outside their lifetime."""

import fcntl
import re
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path

from common import ROOT, download, write_json


@contextmanager
def transient_paths(output: Path, *names: str):
    paths = []
    for name in names:
        relative = Path(name)
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise ValueError("Fixture paths must stay inside the output")
        paths.append(output / relative)
    # A retained lock inode prevents concurrent invocations from acquiring
    # different locks while one fixture is still deleting its private files.
    with (output / ".fixture.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if any(p.exists() or p.is_symlink() for p in paths):
            raise ValueError("Fixture payload paths must be fresh")
        try:
            yield
        finally:
            for path in paths:
                if not path.parent.resolve().is_relative_to(output.resolve()):
                    raise ValueError("Fixture cleanup path escaped its output")
                if path.is_symlink() or path.is_file():
                    path.unlink()
                elif path.exists():
                    shutil.rmtree(path)
            write_json(
                output / "fixture-storage.json", {"cleaned": True, "transient_paths": list(names)}
            )


def copy_runtime(source: Path, destination: Path):
    """Keep fixture writes isolated, including runtimes with linked resources."""
    if (
        destination.exists()
        or destination.is_symlink()
        or destination.resolve().is_relative_to(source.resolve())
    ):
        raise ValueError("Runtime copy needs a fresh destination outside its source")
    subprocess.run(["cp", "-a", "--reflink=auto", "--", str(source), str(destination)], check=True)
    resources = destination / "resources"
    if resources.is_symlink() or resources.is_file():
        resources.unlink()
    elif resources.exists():
        shutil.rmtree(resources)
    resources.mkdir()


@contextmanager
def client_archive(source: dict, cache: Path | None = None):
    """Share only checksum-verified upstream bytes with the managed dev cache."""
    expected = source["sha256"]
    if not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise ValueError("Invalid client archive SHA-256")
    cache = cache or ROOT / "out/dev/downloads"
    cache.mkdir(parents=True, exist_ok=True)
    with (cache / (expected + ".lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield download(source["url"], cache / (expected + ".tar.br"), expected)
