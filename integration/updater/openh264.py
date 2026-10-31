"""Acquire Cisco's verified decoder in the user's cache before sandbox startup."""

import bz2
import fcntl
import hashlib
import http.client
import os
import sys
import tempfile
import urllib.error
from pathlib import Path

from support import RateLimited, checksum, download, progress, read_object

MANIFEST = Path(__file__).with_suffix(".json")
NETWORK_FAILURES = (OSError, http.client.HTTPException)


def valid_library(path: Path, manifest: dict) -> bool:
    try:
        return (
            path.is_file()
            and path.stat().st_size == manifest["library_size"]
            and checksum(path) == manifest["library_sha256"]
        )
    except OSError:
        return False


def fetch(archive: Path, manifest: dict):
    for attempt in range(2):
        try:
            download(
                manifest["url"],
                archive,
                limit=manifest["archive_size"],
                label="Downloading OpenH264",
            )
            if archive.stat().st_size != manifest["archive_size"]:
                raise http.client.IncompleteRead(b"")
            break
        except (*NETWORK_FAILURES, ValueError) as error:
            # Retry transport interruption, not an integrity failure or an
            # invalid redirect. A permanent HTTP error needs user intervention.
            transient = not isinstance(error, ValueError) or "incomplete" in str(error)
            if isinstance(error, urllib.error.HTTPError):
                transient = error.code in (408, 429) or error.code >= 500
            if attempt or not transient or isinstance(error, RateLimited):
                raise
            progress("check", "Retrying OpenH264 download")
    if checksum(archive) != manifest["archive_sha256"]:
        raise ValueError("OpenH264 download checksum mismatch")


def ensure(profile: Path, *, offline: bool = False) -> Path:
    manifest = read_object(MANIFEST)
    directory = profile / "dpe-codecs/openh264"
    destination = directory / manifest["filename"]
    if valid_library(destination, manifest):
        return destination
    if offline:
        raise ValueError(
            "OpenH264 is not cached or needs repair. Connect to the internet and launch "
            "without DPE_CLIENT_OFFLINE=1 to prepare software H.264 decoding."
        )
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".dpe-install.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if valid_library(destination, manifest):
            return destination
        progress("check", "Preparing OpenH264")
        try:
            with tempfile.TemporaryDirectory(prefix=".dpe-openh264-", dir=directory) as work:
                archive = Path(work) / "codec.bz2"
                fetch(archive, manifest)
                decoder = bz2.BZ2Decompressor()
                library = decoder.decompress(
                    archive.read_bytes(), max_length=manifest["library_size"] + 1
                )
                if (
                    len(library) != manifest["library_size"]
                    or not decoder.eof
                    or decoder.unused_data
                    or hashlib.sha256(library).hexdigest() != manifest["library_sha256"]
                ):
                    raise ValueError("OpenH264 library checksum or size mismatch")
                staged = Path(work) / manifest["filename"]
                with staged.open("wb") as output:
                    output.write(library)
                    output.flush()
                    os.fsync(output.fileno())
                staged.chmod(0o644)
                # Replace only after full verification. An interrupted download
                # cannot expose a partial library to the dynamic loader.
                staged.replace(destination)
                folder = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(folder)
                finally:
                    os.close(folder)
        except (*NETWORK_FAILURES, ValueError, EOFError) as error:
            raise ValueError(
                f"Could not prepare OpenH264: {error}. Check your connection and retry."
            ) from error
    return destination


def ensure_requested(profile: Path):
    """The launcher supplies the final flag choice, including recovery overrides."""
    requested = os.environ.get("DPE_OPENH264_LIBRARY", "")
    manifest = read_object(MANIFEST)
    managed = profile / "dpe-codecs/openh264" / manifest["filename"]
    if not requested or Path(requested) != managed:
        # A custom library remains the user's choice; never replace or download
        # into arbitrary paths selected by launch flags.
        return None
    offline = os.environ.get("DPE_OPENH264_OFFLINE", os.environ.get("DPE_CLIENT_OFFLINE")) == "1"
    return ensure(profile, offline=offline)


if __name__ == "__main__":
    try:
        ensure_requested(Path(sys.argv[1]))
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
