"""Bounded local updater events and a transparent launcher output relay."""

import fcntl
import json
import os
import stat
import sys
import time
from pathlib import Path

LIMIT = 4 * 1024 * 1024


def append(profile, filename, text):
    if not profile:
        return
    try:
        directory = Path(profile) / "logs/dpe"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        file = directory / filename
        lock = os.open(
            str(file) + ".lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600
        )
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if file.exists() and file.lstat().st_size >= LIMIT:
                file.with_name(file.name + ".2").unlink(missing_ok=True)
                if file.with_name(file.name + ".1").exists():
                    file.with_name(file.name + ".1").replace(file.with_name(file.name + ".2"))
                file.replace(file.with_name(file.name + ".1"))
            fd = os.open(
                file, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600
            )
            try:
                if stat.S_ISREG(os.fstat(fd).st_mode):
                    data = text.encode("utf-8", errors="replace")
                    os.write(fd, data if len(data) <= 65536 else b"Oversized diagnostic omitted\n")
            finally:
                os.close(fd)
        finally:
            os.close(lock)
    except OSError:
        pass


def event(stage, **details):
    try:
        text = json.dumps({"time": time.time(), "stage": stage, **details}) + "\n"
    except (TypeError, ValueError):
        return
    append(os.environ.get("DPE_PROFILE_DIR"), "updater.log", text)


def relay(profile, stream):
    pending = b""
    oversized = False
    forwarding = True
    while chunk := os.read(sys.stdin.fileno(), 16384):
        if forwarding:
            try:
                sys.stdout.buffer.write(chunk)
                sys.stdout.buffer.flush()
            except BrokenPipeError:
                forwarding = False
        for part in chunk.splitlines(keepends=True):
            if not oversized and len(pending) + len(part) <= 65536:
                pending += part
            else:
                pending = b""
                oversized = True
            if part.endswith(b"\n"):
                append(
                    profile,
                    f"launcher-{stream}.log",
                    "Oversized log line omitted\n"
                    if oversized
                    else pending.decode(errors="replace"),
                )
                pending = b""
                oversized = False
    if pending:
        append(profile, f"launcher-{stream}.log", pending.decode(errors="replace") + "\n")


if __name__ == "__main__":
    relay(sys.argv[1], sys.argv[2])
