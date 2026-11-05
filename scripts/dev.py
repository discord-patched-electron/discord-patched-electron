"""Manage private DPE development versions and independent Discord profiles."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from common import ROOT, read_json, sha256, write_json

DEFAULT_HOME = ROOT / "out/dev"


def name(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", value):
        raise ValueError("Names need 1–64 letters, digits, underscores or hyphens")
    return value


@contextlib.contextmanager
def lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError(f"In use by another dev manager: {path.stem}") from error
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def files(root: Path):
    """Do not follow profile locks, external links or runtime/resources."""
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(directory) / d).is_symlink()]
        for filename in names:
            path = Path(directory) / filename
            if not path.is_symlink() and path.is_file():
                yield path


def size(root: Path) -> str:
    total = sum(p.stat().st_blocks * 512 for p in files(root))
    return f"{total / 1024**3:.2f} GiB"


def editable(path: Path) -> bool:
    relative = path.relative_to(ROOT)
    return relative.parts[0] == "integration" and (
        path.suffix in (".cjs", ".js", ".jsx", ".css", ".svg", ".html")
        or (path.suffix == ".py" and relative.parts[1] in ("audio", "video", "camera", "desktop"))
    )


def copy_changed(source: Path, destination: Path):
    if destination.is_file() and source.read_bytes() == destination.read_bytes():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        shutil.copy2(source, temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def sync_sidecars(app: Path, sidecars: dict):
    audio = app / "integration/audio"
    for source, destination in sidecars.items():
        copy_changed(audio / source, audio / destination)


class Manager:
    def __init__(self, home: Path):
        self.home = home.resolve()
        self.home.mkdir(parents=True, exist_ok=True)
        self.home.chmod(0o700)
        for directory in ("versions", "profiles", "objects", "downloads", "locks"):
            path = self.home / directory
            if path.is_symlink():
                raise ValueError(f"Managed directory must not be a symlink: {path}")
            path.mkdir(exist_ok=True)

    def path(self, kind: str, label: str) -> Path:
        path = self.home / kind / name(label)
        if path.is_symlink():
            raise ValueError(f"Managed entry must not be a symlink: {path}")
        return path

    def version(self, label: str) -> Path:
        root = self.path("versions", label)
        if not (root / "dev-version.json").is_file():
            raise ValueError(f"Unknown version: {label}; use create first")
        return root

    def profile(self, label: str) -> Path:
        root = self.path("profiles", label)
        root.mkdir(mode=0o700, exist_ok=True)
        return root

    def pool_runtime(self, app: Path):
        """Only immutable Electron files are linked; resources and modules stay private."""
        for source in files(app / "runtime"):
            mode = source.stat().st_mode & 0o777
            # Never pool writable sandbox helpers with privileged permission bits.
            if source.stat().st_mode & 0o7000:
                continue
            target = self.home / "objects" / (sha256(source) + f"-{mode & 0o555:o}")
            if not target.exists():
                copy_changed(source, target)
                target.chmod(mode & 0o555)
            elif sha256(target) != sha256(source):
                raise ValueError(f"Damaged shared Electron object: {target.name}")
            temporary = source.with_name(source.name + ".dev-link")
            try:
                os.link(target, temporary)
                temporary.replace(source)
            finally:
                temporary.unlink(missing_ok=True)

    def share_installed_runtime(self, app: Path, profile: Path):
        # Publishing uses isolated copies for normal installs. In a managed
        # profile only, fold identical immutable Electron files back into the pool.
        trees = list((profile / "dpe-updater/installations").glob("*"))
        trees += list(profile.glob("app-*"))
        for original in files(app / "runtime"):
            if original.stat().st_mode & 0o7222:
                continue
            relative = original.relative_to(app / "runtime")
            for tree in trees:
                candidate = tree / relative
                if candidate.is_symlink() or not candidate.is_file():
                    continue
                if candidate.stat().st_ino == original.stat().st_ino:
                    continue
                if candidate.stat().st_size != original.stat().st_size:
                    continue
                if sha256(candidate) != sha256(original):
                    continue
                temporary = candidate.with_name(candidate.name + ".dev-link")
                try:
                    os.link(original, temporary)
                    temporary.replace(candidate)
                finally:
                    temporary.unlink(missing_ok=True)

    def create(self, label: str, artifact: Path, clients: Path):
        from build_local_app import build

        destination = self.path("versions", label)
        with lock(self.home / "locks/catalog"), lock(self.home / "locks" / (label + ".version")):
            if destination.exists():
                raise ValueError("Version exists; choose another name")
            try:
                # Build at its permanent path: legacy ASAR bootstraps embed this path.
                build(
                    destination, "canary", artifact, self.home / "downloads", clients, portable=True
                )
                self.freeze_client(destination)
                self.pool_runtime(destination)
                write_json(destination / "dev-version.json", {"schema": 1, "created": time.time()})
            except BaseException:
                if destination.exists():
                    shutil.rmtree(destination)
                raise
        print(f"Created {label}. Run: bun run dev run {label} --profile dev --watch")

    @staticmethod
    def freeze_client(app: Path):
        # Native/script/lock changes require a new version. This private copy
        # keeps helper edits from creating another complete native generation.
        installation = app / "integration/updater/installation.py"
        source = installation.read_text()
        before = 'files[relative] = {"New": {"Sha256": checksum(item)}}'
        after = (
            'files[relative] = {"New": {"Sha256": '
            "hashlib.sha256(os.readlink(item).encode()).hexdigest() "
            "if item.is_symlink() else checksum(item)}}"
        )
        if source.count(before) != 1:
            raise ValueError("Unsupported development installation manifest layout")
        # Editable symlinks are indexed by their destination, as in module_files.
        # Their contents change deliberately; native files keep content hashes.
        installation.write_text(
            source.replace(before, after).replace(
                "if item.is_file():", 'if item.is_file() and item.suffix != ".log":'
            )
        )
        client = app / "integration/updater/client.py"
        identity = subprocess.check_output(
            [
                sys.executable,
                "-c",
                "import sys; sys.path.insert(0, sys.argv[1]); "
                "import client; print(client.compatibility_key())",
                str(app / "integration/updater"),
            ],
            text=True,
        ).strip()
        client.write_text(
            client.read_text() + "\n\ndef compatibility_key():\n    return " + repr(identity) + "\n"
        )

    def import_app(self, label: str, source: Path):
        source = source.resolve()
        metadata = read_json(source / "local-build.json")
        if not metadata.get("portable") or metadata.get("runtime_kind") != "compiled":
            raise ValueError("Import needs a portable compiled local app; legacy apps embed paths")
        if (source / "dev-version.json").exists():
            raise ValueError("Import an original portable app, not another managed version")
        if sha256(source / "runtime/electron") != metadata["executable_sha256"]:
            raise ValueError("Electron differs from the local build record")
        destination = self.path("versions", label)
        with lock(self.home / "locks/catalog"):
            if destination.exists():
                raise ValueError("Version exists; choose another name")
            try:
                subprocess.run(
                    ["cp", "-a", "--reflink=auto", "--", str(source), str(destination)], check=True
                )
                self.freeze_client(destination)
                self.pool_runtime(destination)
                write_json(
                    destination / "dev-version.json",
                    {"schema": 1, "created": time.time(), "imported_from": str(source)},
                )
            except BaseException:
                if destination.exists():
                    shutil.rmtree(destination)
                raise
        print(f"Imported {label}. Original files are unchanged.")

    def sync(self, app: Path):
        """Refresh owned JS/CSS only, without repatching native binaries."""
        from build_ui import build as build_ui
        from patch_app_audio import SIDECARS

        with tempfile.TemporaryDirectory(prefix="dpe-dev-ui-") as temporary:
            bundle = build_ui(Path(temporary))
            for directory in ("audio", "video", "camera", "desktop", "discord-ui", "updater"):
                for source in files(ROOT / "integration" / directory):
                    if not editable(source):
                        continue
                    copy_changed(source, app / source.relative_to(ROOT))
            copy_changed(bundle / "config-app.js", app / "integration/updater/config-app.js")
            for relative in ("integration/discord-ui", "integration/audio/discord-ui"):
                for filename in ("ui-runtime.cjs", "PREACT-LICENSE.txt"):
                    copy_changed(bundle / filename, app / relative / filename)
            sync_sidecars(app, SIDECARS)

    def stamp(self):
        # Poll metadata rather than reading every source on each tick.
        roots = (ROOT / "integration", ROOT / "scripts", ROOT / "config")
        return {
            str(p): (p.stat().st_mtime_ns, p.stat().st_size)
            for root in roots
            for p in files(root)
            if "__pycache__" not in p.parts and p.suffix != ".pyc"
        }

    def run(self, label: str, profile: str, watch: bool, sync: bool, args: list[str]):
        app = self.version(label)
        with (
            lock(self.home / "locks" / (label + ".version")),
            lock(self.home / "locks" / (name(profile) + ".profile")),
        ):
            data = self.profile(profile)
            environment = dict(os.environ)
            environment.update(
                DPE_PROFILE_DIR=str(data),
                DPE_APPLICATION_UPDATES="0",
                DPE_CLIENT_UPDATES="0",
                DPE_RUNTIME_UPDATES="0",
                DPE_RUNTIME_BUNDLED="1",
                DPE_CLIENT_PINNED="1",
            )
            # Never inherit a previous installation's relaunch or loader state.
            for key in (
                "DPE_LAUNCHER",
                "DPE_APP_ROOT",
                "DPE_RUNTIME_SOURCE",
                "DPE_CLIENT_HOST",
                "DPE_APPLICATION_STARTED",
                "DPE_APPLICATION_VERSION",
            ):
                environment.pop(key, None)
            relaunch = app / "dev-relaunch.sh"
            relaunch.write_text(f"#!/bin/sh\nkill -USR1 {os.getpid()}\n")
            relaunch.chmod(0o700)
            environment["DPE_LAUNCHER"] = str(relaunch)
            previous = self.stamp()
            child = None
            interrupted = False
            requested_restart = False

            def interrupt(_signum, _frame):
                nonlocal interrupted
                interrupted = True

            def request_restart(_signum, _frame):
                nonlocal requested_restart
                requested_restart = True

            handlers = {s: signal.signal(s, interrupt) for s in (signal.SIGTERM, signal.SIGINT)}
            handlers[signal.SIGUSR1] = signal.signal(signal.SIGUSR1, request_restart)
            try:
                while not interrupted:
                    requested_restart = False
                    if sync or watch:
                        self.sync(app)
                    with lock(self.home / "locks/catalog"):
                        subprocess.run(
                            [
                                sys.executable,
                                str(ROOT / "scripts/dev_client.py"),
                                str(app),
                                str(data),
                                str(self.home / "downloads"),
                            ],
                            env={**environment, "PYTHONPATH": str(app / "python")},
                            check=True,
                        )
                    if interrupted:
                        break
                    self.share_installed_runtime(app, data)
                    print(f"Running {label} with profile {profile}. Ctrl+C stops it.", flush=True)
                    child = subprocess.Popen(
                        [str(app / "launch.sh"), *args], env=environment, start_new_session=True
                    )
                    restart = False
                    while child.poll() is None and not interrupted:
                        time.sleep(0.5)
                        current = self.stamp() if watch else previous
                        if current != previous:
                            # Wait for a quiet write interval before rebuilding the bundles.
                            time.sleep(0.5)
                            settled = self.stamp()
                            if settled != current:
                                continue
                            changed = {
                                p
                                for p in previous.keys() | settled.keys()
                                if previous.get(p) != settled.get(p)
                            }
                            previous = settled
                            refresh = {p for p in changed if editable(Path(p))}
                            if changed - refresh:
                                print(
                                    "Build inputs changed; create a new version to use them.",
                                    flush=True,
                                )
                            if not refresh:
                                continue
                            if any(not Path(p).exists() for p in refresh):
                                print(
                                    "Source removed; create a new version to update the file inventory.",
                                    flush=True,
                                )
                                continue
                            restart = True
                            print("Sources changed; restarting Discord…", flush=True)
                            break
                    if child.poll() is not None and not interrupted:
                        time.sleep(0.8)  # app.relaunch starts its launcher after exit
                    restart = restart or requested_restart
                    self.stop(child)
                    if not restart:
                        return child.returncode if not interrupted else 0
            finally:
                if child:
                    self.stop(child)
                for sig, handler in handlers.items():
                    signal.signal(sig, handler)
        return 0

    @staticmethod
    def stop(child):
        # Include Chromium children, even if the launcher already exited.
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            child.wait(timeout=8)
        except subprocess.TimeoutExpired:
            pass
        # A helper may outlive the Electron parent; release the profile only
        # after terminating the entire managed session.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(child.pid, signal.SIGKILL)
        child.wait()

    def inventory(self):
        print(f"Development home: {self.home}")
        for kind in ("versions", "profiles"):
            print(f"\n{kind.capitalize()}:")
            entries = sorted((self.home / kind).iterdir())
            for entry in entries:
                if entry.is_symlink():
                    continue
                print(f"  {entry.name:32} {size(entry)}")
            if not entries:
                print("  None yet")
        print(f"\nDownloads: {size(self.home / 'downloads')}")
        print(f"Shared Electron objects: {size(self.home / 'objects')}")
        print("Sizes include shared links; they must not be added together.")
        print("\nUse run VERSION --profile dev --watch, or --help for all commands.")

    def remove(self, kind: str, label: str):
        path = self.path(kind, label)
        suffix = "version" if kind == "versions" else "profile"
        with lock(self.home / "locks/catalog"), lock(self.home / "locks" / (label + "." + suffix)):
            if not path.is_dir():
                raise ValueError(f"Unknown {suffix}: {label}")
            shutil.rmtree(path)
        print(f"Removed {suffix} {label}")

    def prune(self, apply: bool, downloads: bool):
        with lock(self.home / "locks/catalog"):
            candidates = [p for p in files(self.home / "objects") if p.stat().st_nlink == 1]
            if downloads:
                candidates.extend(files(self.home / "downloads"))
            for path in candidates:
                print(("Removing " if apply else "Would remove ") + str(path))
                if apply:
                    path.unlink()
            print(f"{len(candidates)} files. " + ("Done." if apply else "Use --apply to remove."))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=DEFAULT_HOME)
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("list", help="Show profiles, versions and disk use")
    create = commands.add_parser("create", help="Build once using a retained verified runtime")
    create.add_argument("name")
    create.add_argument("--artifact", type=Path, required=True)
    create.add_argument("--clients", type=Path, default=ROOT / "config/clients.lock.json")
    adopt = commands.add_parser("import", help="Reuse an existing portable compiled app")
    adopt.add_argument("name")
    adopt.add_argument("app", type=Path)
    run = commands.add_parser("run", help="Launch a version with an independent profile")
    run.add_argument("name")
    run.add_argument("--profile", default="dev")
    run.add_argument("--watch", action="store_true", help="Sync and restart when sources change")
    run.add_argument("--sync", action="store_true", help="Sync helper sources before launching")
    remove = commands.add_parser("remove", help="Delete one managed version or profile")
    remove.add_argument("kind", choices=("versions", "profiles"))
    remove.add_argument("name")
    prune = commands.add_parser("prune", help="Preview removal of unused shared files")
    prune.add_argument("--apply", action="store_true")
    prune.add_argument(
        "--downloads", action="store_true", help="Also evict reusable download archives"
    )
    commands.add_parser(
        "doctor", help="Inventory historical out/ directories without deleting them"
    )
    raw = list(argv) if argv is not None else sys.argv[1:]
    forwarded = []
    if "--" in raw:
        index = raw.index("--")
        forwarded, raw = raw[index + 1 :], raw[:index]
    args = parser.parse_args(raw)
    args.command = args.command or "list"
    if forwarded and args.command != "run":
        parser.error("Only run accepts Electron arguments after --")
    try:
        manager = Manager(args.home)
        match args.command:
            case "create":
                manager.create(args.name, args.artifact, args.clients)
            case "import":
                manager.import_app(args.name, args.app)
            case "run":
                return manager.run(args.name, args.profile, args.watch, args.sync, forwarded)
            case "list":
                manager.inventory()
            case "remove":
                manager.remove(args.kind, args.name)
            case "prune":
                manager.prune(args.apply, args.downloads)
            case "doctor":
                for path in sorted((ROOT / "out").iterdir()):
                    if path.is_dir() and not path.is_symlink() and path.resolve() != manager.home:
                        print(f"{size(path):>12}  {path}")
                print("Historical outputs are unmanaged; inspect them before manual removal.")
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"dev: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
