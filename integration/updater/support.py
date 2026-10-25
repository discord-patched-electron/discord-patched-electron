"""Shared I/O boundaries for the independent Electron and Discord updaters."""

import hashlib
import json
import os
import tempfile
import threading
import time
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlparse

_progress_lock = threading.Lock()


def progress(stage: str, label: str, **details):
    if os.environ.get("DPE_UPDATE_PROGRESS") == "1":
        with _progress_lock:
            print(
                json.dumps({"type": "progress", "stage": stage, "label": label, **details}),
                flush=True,
            )


def checksum(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def object_value(value, label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def read_object(path: Path) -> dict:
    return object_value(json.loads(path.read_text()), path.name)


def write_json(path: Path, value):
    """Publish complete metadata and clean up even when replacement fails."""
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write((json.dumps(value, sort_keys=True) + "\n").encode())
            output.flush()
            os.fsync(output.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def replace_damaged(staged: Path, destination: Path):
    """Retain damaged cache entries and publish only a fully prepared replacement."""
    backup = None
    if destination.exists() or destination.is_symlink():
        backup = destination.with_name(destination.name + ".damaged-" + uuid.uuid4().hex)
        destination.rename(backup)
    try:
        staged.rename(destination)
    except OSError:
        if backup is not None:
            backup.rename(destination)
        raise


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlparse(newurl).scheme != "https":
            raise ValueError("Update download redirect must use HTTPS")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url: str, target: Path, *, limit: int = 4 * 1024 * 1024, label: str | None = None):
    if urlparse(url).scheme != "https":
        raise ValueError("Update downloads must use HTTPS")
    opener = urllib.request.build_opener(HTTPSRedirect())
    opener.addheaders = [("User-Agent", "discord-patched-electron-updater/1")]
    with opener.open(url, timeout=20) as response, target.open("wb") as output:
        total = 0
        size = int(response.headers.get("Content-Length", "0"))
        if size < 0 or size > limit:
            raise ValueError("Update download exceeds size limit")
        reported = 0
        while block := response.read(1024 * 1024):
            total += len(block)
            if total > limit:
                raise ValueError("Update download exceeds size limit")
            output.write(block)
            if label and time.monotonic() - reported >= 0.1:
                progress("download", label, current=total, total=size)
                reported = time.monotonic()
        if size and total != size:
            raise ValueError("Update download was incomplete; check your connection and try again")
        if label:
            progress("download", label, current=total, total=size or total)
