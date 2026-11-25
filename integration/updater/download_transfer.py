"""Bounded downloads for checksum-pinned installer assets."""

import hashlib
import http.client
import json
import math
import os
import re
import tempfile
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

MIB = 1024 * 1024
SPLIT_AT = 8 * MIB
CONNECTIONS = 8
SAMPLE_BYTES = 512 * 1024
_range_slots = threading.BoundedSemaphore(12)
NETWORK_ERRORS = (
    urllib.error.URLError,
    TimeoutError,
    ConnectionError,
    http.client.HTTPException,
)


class IncompleteDownload(http.client.IncompleteRead, ValueError):
    def __init__(self, remaining):
        super().__init__(b"", remaining)

    def __str__(self):
        return "Update download was incomplete; check your connection and try again"


class RangeUnavailable(ValueError):
    pass


def no_cancel():
    pass


def split_downloads_enabled(profile):
    if profile:
        try:
            settings = json.loads((Path(profile) / "settings.json").read_text())
            preferences = settings.get("openasar") if isinstance(settings, dict) else None
            if isinstance(preferences, dict):
                return preferences.get("splitDownloads") is not False
        except (OSError, ValueError):
            pass
    return True


def throttled(callback):
    last = float("-inf")
    highest = 0

    def report(current, total, *, done=False):
        nonlocal last, highest
        now = time.monotonic()
        if callback is not None and (done or now - last >= 0.1):
            highest = max(highest, current)
            callback(min(highest, total) if total else highest, total)
            last = now

    return report


def request(url, start, end, etag=None):
    headers = {"Range": f"bytes={start}-{end}", "Accept-Encoding": "identity"}
    if etag:
        headers["If-Range"] = etag
    return urllib.request.Request(url, headers=headers)


def range_position(response):
    value = response.headers.get("Content-Range", "")
    match = re.fullmatch(r"bytes ([0-9]{1,12})-([0-9]{1,12})/([0-9]{1,12})", value)
    if (
        response.status != 206
        or not match
        or response.headers.get("Content-Encoding", "identity").lower() != "identity"
    ):
        raise RangeUnavailable("Invalid byte range response")
    return tuple(map(int, match.groups()))


def read_block(response, size=256 * 1024):
    return getattr(response, "read1", response.read)(size)


def response_size(response, limit):
    if getattr(response, "status", 200) != 200:
        raise ValueError("Expected a complete download response")
    expected = response.headers.get("Content-Length")
    size = int(expected) if expected is not None else None
    if size is not None and not 0 <= size <= limit:
        raise ValueError("Update download exceeds size limit")
    return size


def stream(response, output, limit, size, check_cancelled, report, prefix=b""):
    digest = hashlib.sha256(prefix)
    output.write(prefix)
    total = len(prefix)
    while True:
        check_cancelled()
        block = read_block(response)
        if not block:
            break
        total += len(block)
        if total > limit:
            raise ValueError("Update download exceeds size limit")
        output.write(block)
        digest.update(block)
        report(total, size or 0)
    if size is not None and total != size:
        raise IncompleteDownload(max(0, size - total))
    return total, digest.hexdigest()


def write_at(fd, offset, block):
    remaining = memoryview(block)
    while remaining:
        written = os.pwrite(fd, remaining, offset)
        if not written:
            raise OSError("Could not write downloaded range")
        remaining = remaining[written:]
        offset += written


def connection_count(size, sampled, elapsed):
    if not sampled:
        return 1
    # Aim for about 1.25 seconds of work per connection. This also keeps fast
    # connections on the original stream and caps the cost of extra TLS setup.
    predicted = (size - sampled) * elapsed / sampled
    return min(CONNECTIONS, size // (SPLIT_AT // 2), max(1, math.ceil(predicted / 1.25)))


def sample_response(response, check_cancelled):
    started = time.monotonic()
    prefix = bytearray()
    limit = min(SAMPLE_BYTES, SPLIT_AT // 4)
    while len(prefix) < limit:
        check_cancelled()
        block = read_block(response, limit - len(prefix))
        if not block:
            break
        prefix.extend(block)
        if time.monotonic() - started >= 0.2:
            break
    return prefix, time.monotonic() - started


def download_parts(opener, initial, output, size, prefix, count, check_cancelled, report):
    url = initial.geturl()
    etag = initial.headers.get("ETag", "")
    etag = etag if re.fullmatch(r'"[^"]*"', etag) else None
    output.truncate(size)
    fd = output.fileno()
    offsets = [size * index // count for index in range(count + 1)]
    progress = offsets[:-1].copy()
    write_at(fd, 0, prefix)
    progress[0] = len(prefix)
    stopped = threading.Event()

    def part(index):
        position, end = progress[index], offsets[index + 1]
        with _range_slots:
            for attempt in range(2):
                if stopped.is_set():
                    return
                original = index == 0 and attempt == 0
                try:
                    response = (
                        initial
                        if original
                        else opener.open(request(url, position, end - 1, etag), timeout=10)
                    )
                    with response as response:
                        if not original and (
                            range_position(response) != (position, end - 1, size)
                            or (etag and response.headers.get("ETag") != etag)
                        ):
                            raise RangeUnavailable("Byte range changed during download")
                        while not stopped.is_set():
                            if original and position == end:
                                return
                            remaining = end - position
                            block = read_block(
                                response, min(256 * 1024, remaining + (not original))
                            )
                            if not block:
                                if remaining:
                                    raise IncompleteDownload(remaining)
                                return
                            if len(block) > remaining:
                                raise RangeUnavailable("Byte range exceeded its declared length")
                            write_at(fd, position, block)
                            position += len(block)
                            progress[index] = position
                except urllib.error.HTTPError as error:
                    if limited(error) or error.code not in (408, 500, 502, 503, 504):
                        raise
                    error.close()
                    if attempt or position == end:
                        raise RangeUnavailable("Byte range was interrupted") from error
                except NETWORK_ERRORS as error:
                    if attempt or position == end:
                        raise RangeUnavailable("Byte range was interrupted") from error

    with ThreadPoolExecutor(max_workers=count) as workers:
        tasks = {workers.submit(part, index) for index in range(count)}
        pending = tasks
        try:
            while pending:
                check_cancelled()
                done, pending = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
                if any(task.exception() for task in done):
                    break
                report(sum(progress) - sum(offsets[:-1]), size)
        finally:
            stopped.set()
    errors = [error for task in tasks if (error := task.exception()) is not None]
    for error in errors:
        if isinstance(error, urllib.error.HTTPError) and limited(error):
            raise error
    if errors:
        raise errors[0]
    check_cancelled()
    output.seek(0)
    return size, hashlib.file_digest(output, "sha256").hexdigest()


def limited(error):
    return error.code == 429 or (
        error.headers
        and (error.headers.get("Retry-After") or error.headers.get("X-RateLimit-Remaining") == "0")
    )


def range_or_stream(opener, url, output, limit, expected, parallel, check_cancelled, report):
    with opener.open(url, timeout=20) as response:
        size = response_size(response, limit)
        if (
            expected is None
            or not parallel
            or size is None
            or size < SPLIT_AT
            or response.headers.get("Accept-Ranges", "").strip().lower() != "bytes"
            or response.headers.get("Content-Encoding", "identity").lower() != "identity"
        ):
            return stream(response, output, limit, size, check_cancelled, report)
        prefix, elapsed = sample_response(response, check_cancelled)
        count = connection_count(size, len(prefix), elapsed)
        if count == 1:
            return stream(response, output, limit, size, check_cancelled, report, prefix)
        try:
            received, digest = download_parts(
                opener, response, output, size, prefix, count, check_cancelled, report
            )
            if digest == expected:
                return received, digest
        except urllib.error.HTTPError as error:
            # A part may be rejected or its temporary CDN URL can expire.
            # Preserve rate limiting across both the split and fallback paths.
            if limited(error):
                raise
            error.close()
        except RangeUnavailable:
            pass
    check_cancelled()
    output.seek(0)
    output.truncate(0)
    with opener.open(url, timeout=20) as response:
        return stream(
            response, output, limit, response_size(response, limit), check_cancelled, report
        )


def transfer(
    opener,
    url,
    target,
    *,
    limit,
    expected_sha256=None,
    parallel=True,
    on_progress=None,
    check_cancelled=no_cancel,
):
    """Verify the complete file before atomically replacing the destination."""
    temporary = None
    report = throttled(on_progress)
    try:
        check_cancelled()
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as output:
            temporary = Path(output.name)
            size, digest = range_or_stream(
                opener, url, output, limit, expected_sha256, parallel, check_cancelled, report
            )
            if expected_sha256 is not None and digest != expected_sha256:
                raise ValueError("Update download checksum mismatch")
            check_cancelled()
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(target)
        report(size, size, done=True)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
