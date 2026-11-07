"""Collect local diagnostic evidence without copying account databases or settings."""

import gzip
import json
import os
import re
import resource
import stat
import struct
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from diagnostic_inventory import hardware_inventory, state_inventory, system_queries
from log_redaction import Redactor

LOG_LIMIT = 32 * 1024 * 1024
TOTAL_LIMIT = 128 * 1024 * 1024
CRASH_COUNT = 5


def command(args, destination, limit=LOG_LIMIT):
    def restrict():
        resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))

    with destination.open("wb") as output:
        try:
            result = subprocess.run(
                args,
                stdout=output,
                stderr=subprocess.PIPE,
                timeout=5,
                preexec_fn=restrict,
                check=False,
            )
            if result.returncode:
                return result.stderr.decode(errors="replace")[-2000:] or f"Exit {result.returncode}"
            if destination.stat().st_size >= limit:
                return "Output reached the size limit"
        except (OSError, subprocess.TimeoutExpired) as error:
            return str(error)
    return None


def collect(
    profile,
    crash_paths,
    output,
    executable,
    metadata,
    log_paths=(),
    app=None,
    executables=(),
    log_files=(),
):
    redactor = Redactor(profile)
    deadline = time.monotonic() + 45

    def diagnostic_command(args, destination):
        if time.monotonic() >= deadline:
            return "Collection deadline reached"
        return command(args, destination)

    report = {
        "created": datetime.now(UTC).isoformat(),
        "versions": metadata,
        "limits": {
            "log_bytes": LOG_LIMIT,
            "total_bytes": TOTAL_LIMIT,
            "recent_crashes": CRASH_COUNT,
            "system_history": "7 days",
            "collection_seconds": 45,
        },
        "included": [],
        "skipped": [],
        "notes": [
            "Text and metadata are automatically redacted on a best-effort basis. Review before sharing.",
            "Only existing logs are collected; live WebRTC/media-internals recordings are not enabled.",
            "Account stores, settings, raw memory dumps and opaque binary logs are excluded.",
        ],
    }
    state = state_inventory(profile, Path(app) if app else None)
    hardware = hardware_inventory()
    used = 0
    seen = set()
    crashes = []
    logs = []

    visited = 0
    visited_dirs = set()

    def walk(directory, prefix, all_files=False, crash=False, depth=0):
        nonlocal visited
        if depth > 12 or visited > 20000:
            report["skipped"].append({"source": str(directory), "reason": "Scan limit exceeded"})
            return
        try:
            if directory.is_symlink():
                return
            info = directory.stat()
            identity = (info.st_dev, info.st_ino)
            if identity in visited_dirs:
                return
            visited_dirs.add(identity)
            for entry in directory.iterdir():
                visited += 1
                if visited > 20000:
                    raise OSError("Scan limit exceeded")
                if entry.is_symlink():
                    continue
                name = f"{prefix}/{entry.name}"
                if entry.is_dir():
                    if entry.name.lower() not in {
                        "cache",
                        "code cache",
                        "gpucache",
                        "node_modules",
                        "installations",
                        "backups",
                        "downloads",
                    }:
                        walk(
                            entry,
                            name,
                            all_files or entry.name.lower() in {"logs", "log"},
                            crash,
                            depth + 1,
                        )
                elif entry.is_file():
                    if entry.name.endswith(".lock"):
                        continue
                    suffixes = entry.name.lower().split(".")
                    is_crash = (
                        entry.suffix.lower() in {".dmp", ".mdmp", ".core", ".dump"}
                        or entry.name == "core"
                        or entry.name.startswith("core.")
                    )
                    if crash and not is_crash:
                        continue
                    if (
                        is_crash
                        or all_files
                        or "log" in suffixes
                        or entry.name.lower().endswith(".log.gz")
                    ):
                        info = entry.stat()
                        item = (info.st_mtime, entry, name)
                        (crashes if is_crash else logs).append(item)
        except OSError as error:
            report["skipped"].append({"source": str(directory), "reason": str(error)})

    # Dedicated logs include Discord main/renderer, rotating voice/RTC and helper logs.
    walk(profile / "logs", "profile/logs", True)
    for relative in (
        "Vencord/logs",
        "vencord/logs",
        "equicord/logs",
        "Equicord/logs",
        "Crash Reports/attachments",
    ):
        walk(profile / relative, f"profile/{relative}", True)
    # Other known locations may contain native voice logs and Chromium debug logs.
    for directory in [profile, profile / "module_data", profile / "dpe-updater"]:
        if directory == profile:
            try:
                entries = list(directory.iterdir())
            except OSError as error:
                report["skipped"].append({"source": str(directory), "reason": str(error)})
                entries = []
            for entry in entries:
                if entry.is_file() and not entry.is_symlink() and ".log" in entry.name.lower():
                    logs.append((entry.stat().st_mtime, entry, f"profile/{entry.name}"))
                elif entry.is_dir() and (
                    entry.name[:1].isdigit() or re.fullmatch(r"app-\d+\.\d+\.\d+", entry.name)
                ):
                    walk(entry, f"profile/{entry.name}")
        else:
            walk(directory, f"profile/{directory.name}")
    # Module views are commonly symlinks. Follow only the explicit modules link,
    # not arbitrary links encountered while walking account/profile trees.
    for host in sorted(profile.glob("app-*"))[:8]:
        if (
            host.is_dir()
            and not host.is_symlink()
            and re.fullmatch(r"app-\d+\.\d+\.\d+", host.name)
        ):
            modules = host / "modules"
            if modules.exists():
                walk(modules.resolve(), f"modules/{host.name}")
    for index, file in enumerate(dict.fromkeys(log_files)):
        file = Path(file)
        if file.is_absolute() and file.is_file() and not file.is_symlink():
            logs.append((file.stat().st_mtime, file, f"explicit-logs/{index}-{file.name}"))
    for index, directory in enumerate(dict.fromkeys(log_paths)):
        walk(Path(directory), f"extra-logs/{index}")
    for index, directory in enumerate(dict.fromkeys(crash_paths)):
        walk(Path(directory), f"crashes/{index}", True, True)

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:

        def add(source, name, limit, tail=False):
            nonlocal used
            try:
                if time.monotonic() >= deadline:
                    raise ValueError("Collection deadline reached")
                # Do not follow files replaced by symlinks during collection.
                with os.fdopen(
                    os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb"
                ) as stream:
                    info = os.fstat(stream.fileno())
                    identity = (info.st_dev, info.st_ino)
                    if identity in seen:
                        return
                    if not stat.S_ISREG(info.st_mode):
                        raise ValueError("Not a regular file")
                    seen.add(identity)
                    size = min(info.st_size, limit) if tail else info.st_size
                    if size > limit or used + size > TOTAL_LIMIT:
                        raise ValueError("Size limit exceeded")
                    # Bound reads even when a live log keeps growing.
                    if size < info.st_size:
                        offset = info.st_size - size
                        stream.seek(offset - 1)
                        if stream.read(1) != b"\n":
                            discarded = stream.readline(min(size, 65536))
                            if not discarded.endswith(b"\n"):
                                raise ValueError("No safe text boundary in log tail")
                            size -= len(discarded)
                        report["skipped"].append(
                            {
                                "source": str(source),
                                "reason": "Older log bytes omitted",
                                "bytes": info.st_size - size,
                            }
                        )
                    if source.suffix.lower() == ".gz":
                        if size < info.st_size:
                            raise ValueError("Compressed log exceeds size limit")
                        with gzip.GzipFile(fileobj=stream) as compressed:
                            data = compressed.read(limit + 1)
                        if len(data) > limit:
                            raise ValueError("Decompressed log exceeds size limit")
                        name += ".txt"
                    else:
                        data = stream.read(size)
                    if name == "system/audio-server.log":
                        data = b"\n".join(
                            line
                            for line in data.splitlines()
                            if line.startswith(
                                (
                                    b"pactl ",
                                    b"Server Name:",
                                    b"Server Version:",
                                    b"Default Sample Specification:",
                                    b"Default Channel Map:",
                                )
                            )
                        )
                    data = re.sub(rb"\x1b\[[0-?]*[ -/]*[@-~]", b"", data)
                    if source.suffix.lower() in {
                        ".bin",
                        ".dat",
                        ".wav",
                        ".mp4",
                        ".webm",
                        ".png",
                        ".jpg",
                        ".sqlite",
                    } or any(byte < 9 or 13 < byte < 32 for byte in data):
                        raise ValueError("Binary log omitted: cannot safely redact")
                    try:
                        text = data.decode("utf-8")
                    except UnicodeDecodeError:
                        raise ValueError("Non-text log omitted: cannot safely redact") from None
                    try:
                        text = json.dumps(redactor.value(json.loads(text)), indent=2)
                    except ValueError:
                        # Redact across the complete text so credentials crossing read chunks are covered.
                        lines = []
                        for line in text.splitlines():
                            try:
                                lines.append(json.dumps(redactor.value(json.loads(line))))
                            except (ValueError, RecursionError):
                                lines.append(redactor.text(line))
                        text = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
                    payload = text.encode("utf-8")
                    if used + len(payload) > TOTAL_LIMIT:
                        raise ValueError("Archive size limit exceeded")
                    name = redactor.text(name)
                    if name in archive.namelist():
                        name = f"redacted-log-{len(report['included'])}.txt"
                    archive.writestr(name, payload)
                    used += len(payload)
                    report["included"].append({"file": name, "bytes": len(payload)})
            except (OSError, ValueError, EOFError, RecursionError) as error:
                report["skipped"].append({"source": str(source), "reason": str(error)})

        for _, source, name in sorted(
            logs,
            key=lambda item: (
                item[2].startswith("profile/logs/dpe/"),
                any(part in item[2].lower() for part in ("voice", "rtc")),
                item[0],
            ),
            reverse=True,
        ):
            add(source, name, LOG_LIMIT, tail=True)
        crash_identities = set()
        for _, source, _name in sorted(crashes, reverse=True):
            try:
                with os.fdopen(
                    os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb"
                ) as stream:
                    info = os.fstat(stream.fileno())
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or (info.st_dev, info.st_ino) in crash_identities
                    ):
                        continue
                    if len(crash_identities) >= CRASH_COUNT:
                        break
                    crash_identities.add((info.st_dev, info.st_ino))
                    summary = {
                        "bytes": info.st_size,
                        "modified": datetime.fromtimestamp(info.st_mtime, UTC).isoformat(),
                        "raw_dump": "Excluded: memory cannot be safely redacted",
                    }
                    header = stream.read(32)
                    if header.startswith(b"MDMP") and len(header) == 32:
                        summary["format"] = "minidump"
                        count, offset = struct.unpack_from("<II", header, 8)
                        for index in range(min(count, 128)):
                            stream.seek(offset + index * 12)
                            entry = stream.read(12)
                            if len(entry) != 12:
                                break
                            kind, size, location = struct.unpack("<III", entry)
                            if kind == 6 and size >= 32:
                                stream.seek(location)
                                exception = stream.read(32)
                                if len(exception) == 32:
                                    summary["exception_code"] = hex(
                                        struct.unpack_from("<I", exception, 8)[0]
                                    )
                                break
                    elif header.startswith(b"\x7fELF") and len(header) >= 20:
                        summary["format"] = "ELF core"
                        summary["machine"] = int.from_bytes(
                            header[18:20], "little" if header[5] == 1 else "big"
                        )
                    else:
                        summary["format"] = "Unknown dump"
                    name = f"crashes/crash-{len(crash_identities)}.json"
                    archive.writestr(name, json.dumps(summary, indent=2) + "\n")
                    report["included"].append({"file": name, "kind": "crash summary"})
            except OSError as error:
                report["skipped"].append({"source": str(source), "reason": str(error)})
        with tempfile.TemporaryDirectory(prefix="dpe-diagnostics-") as temporary:
            directory = Path(temporary)
            candidates = [executable, *executables]
            candidates.extend(
                str(file.resolve())
                for file in sorted(profile.glob("app-*/electron"))[:8]
                if file.is_file()
            )
            if app:
                for relative in (
                    "capture/dpe-wayland-source",
                    "capture/dpe-kms-source",
                    "capture/vkcapture/dpe-vkcapture-source",
                    "capture/vkcapture/vkcapture-broker.py",
                ):
                    file = Path(app) / relative
                    if file.is_file():
                        candidates.append(str(file.resolve()))
            candidates = list(
                dict.fromkeys(
                    file
                    for file in candidates
                    if isinstance(file, str) and Path(file).is_absolute()
                )
            )[:16]
            matches = [
                f"COREDUMP_UID={os.getuid()}",
                *[f"COREDUMP_EXE={file}" for file in candidates],
            ]
            listing = directory / "cores.json"
            error = diagnostic_command(
                ["coredumpctl", "--no-pager", "--since=-7days", "--json=short", "list", *matches],
                listing,
            )
            if error:
                report["skipped"].append({"source": "systemd coredumps", "reason": error})
            else:
                try:
                    records = json.loads(listing.read_text())
                    if not isinstance(records, list) or not all(
                        isinstance(record, dict) for record in records
                    ):
                        raise ValueError("Invalid system crash listing")
                    # An allowlist keeps command lines, hostnames, UIDs and memory out of the report.
                    summaries = [
                        {
                            key: record[key]
                            for key in ("timestamp", "signal", "storage", "size")
                            if key in record
                        }
                        for record in sorted(
                            records, key=lambda item: item.get("timestamp", 0), reverse=True
                        )[:3]
                    ]
                    traced_pids = set()
                    for record, summary in zip(
                        sorted(records, key=lambda item: item.get("timestamp", 0), reverse=True)[
                            :3
                        ],
                        summaries,
                        strict=True,
                    ):
                        if "pid" not in record:
                            continue
                        pid = str(int(record["pid"]))
                        if pid in traced_pids:
                            continue
                        traced_pids.add(pid)
                        details = directory / f"stack-{pid}.txt"
                        error = diagnostic_command(
                            [
                                "coredumpctl",
                                "--no-pager",
                                "--since=-7days",
                                "-1",
                                "info",
                                *matches,
                                f"COREDUMP_PID={pid}",
                            ],
                            details,
                        )
                        if error:
                            report["skipped"].append(
                                {"source": "system crash stack", "reason": error}
                            )
                        else:
                            # Keep stack frames only, excluding environment, command lines and other metadata.
                            summary["stack"] = [
                                redactor.text(line.strip())
                                for line in details.read_text(errors="replace").splitlines()
                                if re.match(r"^\s*#\d+\s+", line)
                            ][:512]
                    archive.writestr(
                        "system/coredumps.json",
                        json.dumps(redactor.value(summaries), indent=2) + "\n",
                    )
                except (ValueError, KeyError, TypeError) as error:
                    report["skipped"].append({"source": "systemd coredumps", "reason": str(error)})
            journal = directory / "journal.log"
            error = diagnostic_command(
                [
                    "journalctl",
                    "--no-pager",
                    "--since=-7days",
                    "-n",
                    "10000",
                    "-o",
                    "short-iso",
                    f"_UID={os.getuid()}",
                    *[f"_EXE={file}" for file in candidates],
                ],
                journal,
            )
            if error:
                report["skipped"].append({"source": "journal", "reason": error})
            else:
                add(journal, "system/journal.log", LOG_LIMIT)
            for name, args in system_queries().items():
                destination = directory / name
                error = diagnostic_command(args, destination)
                if error:
                    report["skipped"].append({"source": f"system/{name}", "reason": error})
                elif destination.exists():
                    add(destination, f"system/{name}", LOG_LIMIT)
        archive.writestr("updater/state.json", json.dumps(redactor.value(state), indent=2) + "\n")
        archive.writestr(
            "system/hardware.json", json.dumps(redactor.value(hardware), indent=2) + "\n"
        )
        archive.writestr("runtime.json", json.dumps(redactor.value(metadata), indent=2) + "\n")
        report["included"].extend(
            {"file": file, "kind": "diagnostic snapshot"}
            for file in ("updater/state.json", "system/hardware.json", "runtime.json")
        )
        coverage = {
            "launcher-bootstrap": {
                "sources": [
                    "profile/logs/dpe/launcher-*.log",
                    "profile/logs/dpe/bootstrap-loader.log",
                    "updater/state.json",
                ],
                "limit": "Older launches may predate persistent logging.",
            },
            "application-client-runtime-updaters": {
                "sources": [
                    "profile/logs/dpe/updater.log",
                    "profile/logs/dpe/runtime.log",
                    "updater/state.json",
                ]
            },
            "main-renderers-gpu-utility": {
                "sources": ["profile/logs", "runtime.json", "system/journal.log", "crashes"]
            },
            "voice-audio-screenshare-codecs": {
                "sources": [
                    "profile/logs",
                    "modules",
                    "extra-logs",
                    "runtime.json",
                    "system/media-services.log",
                    "system/audio-server.log",
                ],
                "limit": "Unsaved WebRTC/media-internals recordings cannot be reconstructed. Audio payloads, SDP and encryption keys are excluded.",
            },
            "direct-game-capture-portals": {
                "sources": [
                    "profile/logs/dpe/runtime.log",
                    "runtime.json",
                    "system/media-services.log",
                    "system/services.log",
                ]
            },
            "camera-desktop-settings-mods": {
                "sources": [
                    "runtime.json",
                    "system/hardware.json",
                    "profile/logs",
                    "updater/state.json",
                ],
                "limit": "Camera frames, previews, arbitrary account/plugin settings and window titles are excluded.",
            },
            "crashes": {
                "sources": ["crashes", "system/coredumps.json"],
                "limit": "Raw memory dumps are excluded; summaries and available stack traces are collected.",
            },
        }
        for section in coverage.values():
            section["files"] = [
                entry["file"]
                for entry in report["included"]
                if any(
                    entry["file"].startswith(prefix.split("*")[0]) for prefix in section["sources"]
                )
            ]
        archive.writestr("coverage.json", json.dumps(redactor.value(coverage), indent=2) + "\n")
        archive.writestr("manifest.json", json.dumps(redactor.value(report), indent=2) + "\n")
    return report


if __name__ == "__main__":
    request = json.load(sys.stdin)
    collect(
        Path(request["profile"]),
        request["crashPaths"],
        Path(sys.argv[1]),
        request["executable"],
        request["metadata"],
        request.get("logPaths", []),
        request.get("app"),
        request.get("executables", []),
        request.get("logFiles", []),
    )
