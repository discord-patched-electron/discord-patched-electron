"""Shared I/O boundaries for the independent Electron and Discord updaters."""

import hashlib
import http.client
import json
import os
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

from diagnostic_log import event as diagnostic_event

_progress_lock = threading.Lock()


def progress(stage: str, label: str, **details):
    diagnostic_event(stage, label=label, **details)
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


class RateLimited(OSError):
    def __init__(self, until):
        self.until = until
        when = datetime.fromtimestamp(until, UTC).strftime("%H:%M UTC")
        super().__init__(f"Download temporarily limited. Try again after {when}.")


class IncompleteDownload(ValueError):
    pass


def retry_time(headers):
    """Respect server cooldowns without sleeping through a whole rate-limit window."""
    now = time.time()
    deadlines = [now + 60]
    value = headers.get("Retry-After", "")
    try:
        deadlines.append(now + max(0, int(value)))
    except (ValueError, TypeError):
        try:
            deadlines.append(parsedate_to_datetime(value).timestamp())
        except (ValueError, TypeError, OverflowError):
            pass
    if headers.get("X-RateLimit-Remaining") == "0":
        try:
            deadlines.append(float(headers.get("X-RateLimit-Reset", "")))
        except (ValueError, TypeError):
            pass
    return max(value for value in deadlines if now <= value <= now + 7 * 86400)


def cooldown_file(url):
    profile = os.environ.get("DPE_PROFILE_DIR")
    if not profile or not Path(profile).is_absolute():
        return None
    host = hashlib.sha256(urlparse(url).netloc.encode()).hexdigest()
    return Path(profile) / "dpe-updater/http-cooldowns" / (host + ".json")


def download(url: str, target: Path, *, limit: int = 4 * 1024 * 1024, label: str | None = None):
    if urlparse(url).scheme != "https":
        raise ValueError("Update downloads must use HTTPS")
    cooldown = cooldown_file(url)
    if cooldown:
        try:
            until = read_object(cooldown)["until"]
            if type(until) in (int, float) and time.time() < until <= time.time() + 7 * 86400:
                raise RateLimited(until)
        except (OSError, ValueError, KeyError, TypeError) as error:
            if isinstance(error, RateLimited):
                raise
    for attempt in range(2):
        try:
            return _download_once(url, target, limit=limit, label=label)
        except urllib.error.HTTPError as error:
            if (
                error.code == 429
                or (
                    error.code == 403
                    and error.headers
                    and (
                        error.headers.get("X-RateLimit-Remaining") == "0"
                        or error.headers.get("Retry-After")
                    )
                )
                or (error.code == 503 and error.headers and error.headers.get("Retry-After"))
            ):
                until = retry_time(error.headers or {})
                if cooldown:
                    try:
                        cooldown.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                        write_json(cooldown, {"until": until})
                    except OSError:
                        pass
                raise RateLimited(until) from error
            if attempt or error.code not in (408, 500, 502, 503, 504):
                raise
        except (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
            http.client.HTTPException,
            IncompleteDownload,
        ):
            if attempt:
                raise
        if label:
            progress("check", f"Retrying {label}")
        time.sleep(0.5)


def _download_once(url, target, *, limit, label):
    opener = urllib.request.build_opener(HTTPSRedirect())
    opener.addheaders = [("User-Agent", "discord-patched-electron-updater/1")]
    # Keep the old complete file usable if the network or local storage fails.
    temporary = None
    try:
        with opener.open(url, timeout=20) as response:
            size = int(response.headers.get("Content-Length", "0"))
            if size < 0 or size > limit:
                raise ValueError("Update download exceeds size limit")
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as output:
                temporary = Path(output.name)
                total = 0
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
                    raise IncompleteDownload(
                        "Update download was incomplete; check your connection and try again"
                    )
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(target)
            if label:
                progress("download", label, current=total, total=size or total)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
