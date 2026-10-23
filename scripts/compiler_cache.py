"""Pinned, bounded local sccache; CI can transport this directory with actions/cache."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile

from common import ROOT, download, read_json, run, write_json


def install(cache: Path) -> Path:
    config = read_json(ROOT / "config/compiler-cache.json")
    archive = download(config["url"], cache / "tools" / ("sccache-" + config["version"] + ".tar.gz"), config["sha256"])
    # sccache dispatches on argv[0]; renaming the executable enables masquerade
    # mode and makes it mistake itself for a compiler.
    binary = archive.parent / config["version"] / "sccache"
    binary.parent.mkdir(parents=True, exist_ok=True)
    # Extract only the expected regular executable; verify the archive every time.
    with tarfile.open(archive) as bundle:
        member = bundle.getmember(config["member"])
        if not member.isfile():
            raise ValueError("sccache archive member is not a regular file")
        data = bundle.extractfile(member).read()
    if not binary.exists() or binary.read_bytes() != data:
        binary.write_bytes(data)
        binary.chmod(0o755)
    return binary


@contextmanager
def compiler_cache(cache: Path, source: Path, output: Path):
    binary = install(cache)
    config = read_json(ROOT / "config/compiler-cache.json")
    size = os.environ.get("ELECTRON_COMPILER_CACHE_SIZE", config["maximum_size"])
    if size not in ("3G", "32G"):
        raise ValueError("Compiler cache must be bounded to 3G (hosted) or 32G (persistent PC)")
    directory = Path(os.environ.get("ELECTRON_COMPILER_CACHE", str(cache / "sccache"))).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    # One writer/server per local store, also on the persistent PC runner.
    with (directory / ".lock").open("a") as lock, tempfile.TemporaryDirectory(prefix="dpe-sccache-") as temp:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        settings = {"SCCACHE_DIR": str(directory), "SCCACHE_CACHE_SIZE": size,
                    "SCCACHE_BASEDIRS": str(source.resolve()), "SCCACHE_DIRECT": "false",
                    "SCCACHE_IDLE_TIMEOUT": "0",
                    "SCCACHE_CONF": str(Path(temp) / "config.toml"),
                    "SCCACHE_SERVER_UDS": str(Path(temp) / "server.sock")}
        Path(settings["SCCACHE_CONF"]).write_text("")
        # Avoid inheriting a user's remote backend, credentials or unsafe cache mode.
        previous = {key: value for key, value in os.environ.items() if key.startswith("SCCACHE_")}
        for key in previous:
            del os.environ[key]
        os.environ.update(settings)
        started = False
        try:
            run(binary, "--start-server", timeout=30)
            started = True
            yield binary
        finally:
            try:
                if started:
                    stats = subprocess.check_output([str(binary), "--show-stats", "--stats-format=json"], text=True, timeout=30)
                    write_json(output / "compiler-cache.json", json.loads(stats))
            finally:
                try:
                    if started:
                        run(binary, "--stop-server", timeout=30)
                finally:
                    for key in settings:
                        os.environ.pop(key, None)
                    os.environ.update(previous)
