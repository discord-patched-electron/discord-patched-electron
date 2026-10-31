"""Stable, stdlib-only trust boundary. Application code lives in signed generations."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import http.client
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path, PurePosixPath

API = 1
REPOSITORY = "discord-patched-electron/discord-patched-electron"
FAILURES = (
    OSError,
    ValueError,
    KeyError,
    TypeError,
    subprocess.SubprocessError,
    zipfile.BadZipFile,
    http.client.HTTPException,
)


def read(path):
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must be a JSON object")
    return value


def checksum(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write(path, value):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write((json.dumps(value, sort_keys=True) + "\n").encode())
            stream.flush()
            os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def descriptor(value):
    if (
        not isinstance(value, dict)
        or value.get("bootstrap_api") != API
        or value.get("asset") != "application.zip"
        or not re.fullmatch(r"\d{8}\.\d{6}", value["version"])
        or any(
            not re.fullmatch(r"[a-f0-9]{64}", value[key])
            for key in ("archive_sha256", "index_sha256")
        )
    ):
        raise ValueError("Application update requires a compatible bootstrap package")
    return value


def authorize(directory, package, policy):
    """Only package pins or a signed, manually approved release can supply code."""
    candidate = descriptor(read(directory / "application.json"))
    if candidate == policy["application"]:
        return candidate
    proof = directory / "proof"
    with tempfile.TemporaryDirectory(prefix="dpe-trust-") as temporary:
        keyring = Path(temporary) / "public.gpg"
        subprocess.run(
            [
                "gpg",
                "--batch",
                "--homedir",
                temporary,
                "--dearmor",
                "--output",
                str(keyring),
                str(package / "release-signing-key.asc"),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        subprocess.run(
            [
                "gpgv",
                "--homedir",
                temporary,
                "--keyring",
                str(keyring),
                str(proof / "SHA256SUMS.sig"),
                str(proof / "SHA256SUMS"),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
    sums = {}
    for line in (proof / "SHA256SUMS").read_text().splitlines():
        match = re.fullmatch(r"([a-f0-9]{64})  ([A-Za-z0-9._+-]+)", line)
        if not match or match[2] in sums:
            raise ValueError("Invalid signed checksums")
        sums[match[2]] = match[1]
    digest = checksum(proof / "release.json")
    if sums.get("release.json") != digest:
        raise ValueError("Application release signature does not cover its manifest")
    release, approval = read(proof / "release.json"), read(proof / "approval.json")
    version = release["electron_version"]
    track = "prerelease" if re.fullmatch(r"\d+\.\d+\.\d+-(alpha|beta)\.\d+", version) else "stable"
    if (
        release.get("signing_required") is not True
        or release.get("source_dirty") is not False
        or release["channel"] != policy["channel"]
        or track != policy["electron_channel"]
        or release.get("application") != candidate
        or release["version"] != candidate["version"]
        or sums.get(candidate["asset"]) != candidate["archive_sha256"]
        or release["assets"].get(candidate["asset"]) != candidate["archive_sha256"]
        or approval.get("schema") != 1
        or approval.get("manifest_sha256") != digest
        or approval.get("version") != candidate["version"]
        or not isinstance(approval.get("approved_by"), str)
        or not approval["approved_by"].strip()
        or approval.get("tag") != read(proof / "github.json")["tag_name"]
        or read(proof / "github.json").get("prerelease") is not False
        or read(proof / "github.json").get("draft") is not False
    ):
        raise ValueError("Application update has no matching release approval")
    minimum = release["minimum_glibc"]
    if not re.fullmatch(r"\d+\.\d+", minimum) or tuple(
        map(int, os.confstr("CS_GNU_LIBC_VERSION").split()[-1].split("."))
    ) < tuple(map(int, minimum.split("."))):
        raise ValueError("Application update requires a newer system glibc")
    return candidate


def safe_path(value):
    path = PurePosixPath(value)
    if (
        not path.parts
        or path.is_absolute()
        or ".." in path.parts
        or "\\" in value
        or path.as_posix() != value
    ):
        raise ValueError("Unsafe application archive path")
    return path


def validate(directory, candidate):
    app = directory / "app"
    if checksum(app / "application-index.json") != candidate["index_sha256"]:
        raise ValueError("Application file index is damaged")
    index = read(app / "application-index.json")
    actual = set()
    for path in app.rglob("*"):
        if path.is_symlink():
            raise ValueError("Application contains an unexpected link")
        if path.is_file() and path != app / "application-index.json":
            name = path.relative_to(app).as_posix()
            actual.add(name)
            if index.get(name) != checksum(path):
                raise ValueError("Application files are damaged: " + name)
    if actual != set(index) or "launch.sh" not in index:
        raise ValueError("Incomplete application download")
    if not os.access(app / "launch.sh", os.X_OK):
        raise ValueError("Application launcher is not executable")
    return app


def extract(archive, app):
    total, seen = 0, set()
    with zipfile.ZipFile(archive) as source:
        if len(source.infolist()) > 20000:
            raise ValueError("Application archive has too many entries")
        for item in source.infolist():
            name = safe_path(item.filename)
            mode = item.external_attr >> 16
            if item.filename in seen or stat.S_IFMT(mode) not in (0, stat.S_IFREG):
                raise ValueError("Unsafe application archive entry")
            seen.add(item.filename)
            total += item.file_size
            if total > 256 * 1024 * 1024:
                raise ValueError("Application archive exceeds size limit")
            target = app.joinpath(*name.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open(item) as data, target.open("xb") as output:
                shutil.copyfileobj(data, output)
            target.chmod(0o700 if mode & 0o111 else 0o600)


def install(root, package, policy, candidate, archive, proof=None):
    candidate = descriptor(candidate)
    if checksum(archive) != candidate["archive_sha256"]:
        raise ValueError("Application download checksum mismatch")
    destination = root / "generations" / candidate["archive_sha256"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="prepare-", dir=root) as temporary:
        staged = Path(temporary) / "generation"
        staged.mkdir()
        write(staged / "application.json", candidate)
        if proof:
            shutil.copytree(proof, staged / "proof")
        authorize(staged, package, policy)
        extract(archive, staged / "app")
        validate(staged, candidate)
        if destination.exists():
            try:
                validate(destination, authorize(destination, package, policy))
                return destination
            except FAILURES:
                destination.rename(
                    destination.with_name(destination.name + ".damaged-" + uuid.uuid4().hex)
                )
        staged.rename(destination)
    return destination


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith("https://"):
            raise ValueError("Application redirects must use HTTPS")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url, target):
    if not url.startswith(f"https://github.com/{REPOSITORY}/releases/download/"):
        raise ValueError("Unexpected application download URL")
    opener = urllib.request.build_opener(HTTPSRedirect())
    for attempt in range(3):
        complete = False
        try:
            with opener.open(url, timeout=20) as response, target.open("wb") as output:
                length = response.headers.get("Content-Length")
                expected = int(length) if length is not None else None
                if expected is not None and not 0 <= expected <= 64 * 1024 * 1024:
                    raise ValueError("Application download exceeds size limit")
                total = 0
                while block := response.read(1024 * 1024):
                    total += len(block)
                    if total > 64 * 1024 * 1024:
                        raise ValueError("Application download exceeds size limit")
                    output.write(block)
                if expected is not None and total < expected:
                    raise http.client.IncompleteRead(b"", expected - total)
            complete = True
            return
        except (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
            http.client.HTTPException,
        ) as error:
            if attempt == 2 or (
                isinstance(error, urllib.error.HTTPError)
                and error.code not in (408, 429, 500, 502, 503, 504)
            ):
                raise
            print(f"Application download interrupted; retrying: {error}", file=sys.stderr)
        finally:
            if not complete:
                target.unlink(missing_ok=True)
        time.sleep(attempt + 1)


def cache_root(package, profile):
    identity = hashlib.sha256(str(package.resolve()).encode()).hexdigest()[:16]
    return profile / "dpe-application" / identity


def generation(root, digest, package, policy):
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("Invalid application generation")
    directory = root / "generations" / digest
    candidate = authorize(directory, package, policy)
    if candidate["archive_sha256"] != digest:
        raise ValueError("Mismatched application generation")
    return validate(directory, candidate), candidate


def process_identity(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return None if fields[0] in ("Z", "X") else fields[19]
    except (OSError, IndexError):
        return None


def read_state(root):
    path = root / "state.json"
    try:
        state = read(path)
        references = [state.get(key) for key in ("current", "previous", "pending")]
        rejected = state.get("rejected", [])
        if not isinstance(rejected, list):
            raise ValueError("Invalid rejected application list")
        references.extend(rejected)
        instances = state.get("instances", [])
        if not isinstance(instances, list):
            raise ValueError("Invalid application instances")
        records = [(key, state[key]) for key in ("running", "trial") if key in state]
        records.extend(("instance", process) for process in instances)
        for key, process in records:
            if (
                not isinstance(process, dict)
                or type(process["pid"]) is not int
                or process["pid"] <= 0
                or (process["process"] is not None and not re.fullmatch(r"\d+", process["process"]))
                or (key == "trial" and not re.fullmatch(r"[a-f0-9]{32}", process["token"]))
            ):
                raise ValueError("Invalid application process record")
            references.append(process["digest"])
        if any(
            value is not None and not re.fullmatch(r"[a-f0-9]{64}", value) for value in references
        ):
            raise ValueError("Invalid application state reference")
        return state
    except FileNotFoundError:
        return {}
    except (ValueError, TypeError, KeyError) as error:
        # State is a hint, not authority to execute code. Recover using package
        # pins and full file verification; retain the damaged metadata for diagnosis.
        path.rename(root / ("state.damaged-" + uuid.uuid4().hex + ".json"))
        print(f"Recovering damaged application state: {error}", file=sys.stderr)
        return {}


def live_instances(state):
    records = ([state["running"]] if state.get("running") else []) + state.get("instances", [])
    return [
        record
        for record in records
        if record["process"] and process_identity(record["pid"]) == record["process"]
    ]


def record_launch(root, state, digest):
    # Track every launch: single-instance forwarders disappear on the next prune,
    # while independently surviving multi-instance processes keep their generation.
    records = [record for record in live_instances(state) if record["pid"] != os.getpid()]
    records.append({"digest": digest, "pid": os.getpid(), "process": process_identity(os.getpid())})
    state["running"] = records[0]
    state["instances"] = records[1:]
    write(root / "state.json", state)


def select(root, package, policy, *, archive=None, launch=False):
    state_file = root / "state.json"
    state = read_state(root)
    trial = state.get("trial")
    if trial:
        if (root / ("ready-" + trial["token"])).is_file():
            state.update(previous=state.get("current"), current=trial["digest"])
            state.pop("trial")
        elif trial["pid"] == os.getpid() and state.get("pending"):
            # The startup UI handed off to a newer application in this process.
            state.pop("trial")
        elif trial["process"] and process_identity(trial["pid"]) == trial["process"]:
            if launch:
                app, _ = generation(root, trial["digest"], package, policy)
                record_launch(root, state, trial["digest"])
                return app, None
        else:
            state.setdefault("rejected", []).append(trial["digest"])
            state.pop("trial")
        write(state_file, state)
    running = next(
        (record for record in live_instances(state) if record["pid"] != os.getpid()), None
    )
    if launch and running:
        # Forwarders and independent instances must both retain the live code.
        app, _ = generation(root, running["digest"], package, policy)
        record_launch(root, state, running["digest"])
        return app, None
    baseline = policy["application"]
    candidates = [
        state.get("trial", {}).get("digest"),
        state.get("pending"),
        state.get("current"),
        baseline["archive_sha256"],
        state.get("previous"),
    ]
    valid = []
    for digest in dict.fromkeys(candidates):
        if not digest or digest in state.get("rejected", []):
            continue
        try:
            app, incoming = generation(root, digest, package, policy)
            valid.append((incoming["version"], digest, app))
        except FAILURES as error:
            print(f"Application cache unavailable: {error}", file=sys.stderr)
    if not valid and baseline["archive_sha256"] in state.get("rejected", []):
        # Closing the first-run UI (or launching offline before setup finishes)
        # is not evidence of bad code. With no working fallback, retry verified
        # package-pinned bytes rather than redownloading the same application.
        try:
            app, incoming = generation(root, baseline["archive_sha256"], package, policy)
            valid.append((incoming["version"], baseline["archive_sha256"], app))
            state["rejected"].remove(baseline["archive_sha256"])
        except FAILURES:
            pass
    # Explicit local archives still have to match the installed package's pins.
    if not valid or (
        baseline["version"] > max(item[0] for item in valid)
        and baseline["archive_sha256"] not in state.get("rejected", [])
        and os.environ.get("DPE_CLIENT_OFFLINE") != "1"
    ):
        try:
            with tempfile.TemporaryDirectory(prefix="download-", dir=root) as temporary:
                source = archive
                if source is None:
                    if os.environ.get("DPE_CLIENT_OFFLINE") == "1":
                        raise ValueError(
                            "Launch once with internet access to download Discord Patched Electron"
                        )
                    print("Downloading Discord Patched Electron...", file=sys.stderr)
                    source = Path(temporary) / "application.zip"
                    download(policy["url"], source)
                directory = install(root, package, policy, baseline, source)
                valid.append((baseline["version"], baseline["archive_sha256"], directory / "app"))
        except FAILURES:
            if not valid:
                raise
    if not valid:
        raise ValueError("No working application is available; reinstall or retry online")
    _, digest, app = max(valid)
    token = None
    if launch and digest != state.get("current"):
        token = uuid.uuid4().hex
        state["trial"] = {
            "digest": digest,
            "pid": os.getpid(),
            "process": process_identity(os.getpid()),
            "token": token,
        }
        state.pop("pending", None)
    if launch:
        record_launch(root, state, digest)
    return app, token


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--stage", type=Path)
    parser.add_argument("--resolve", action="store_true")
    parser.add_argument("--launch", action="store_true")
    parser.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    package = Path(__file__).resolve().parent
    policy = read(package / "bootstrap.json")
    descriptor(policy["application"])
    config = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    profile = (
        args.profile
        or Path(
            os.environ.get(
                "DPE_PROFILE_DIR",
                str(Path(os.environ.get("DISCORD_USER_DATA_DIR", str(config))) / policy["profile"]),
            )
        )
    ).resolve()
    root = cache_root(package, profile)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.stage:
            manifest = read(args.stage / "release.json")
            incoming = descriptor(manifest["application"])
            app, _ = select(root, package, policy)
            current = read(app.parent / "application.json")
            state = read_state(root)
            if incoming["version"] <= max(
                current["version"], policy["application"]["version"]
            ) or incoming["archive_sha256"] in state.get("rejected", []):
                print(app)
                return
            destination = install(root, package, policy, incoming, args.archive, args.stage)
            state["pending"] = incoming["archive_sha256"]
            write(root / "state.json", state)
            print(destination / "app")
            return
        app, token = select(root, package, policy, archive=args.archive, launch=args.launch)
    if not args.launch:
        print(app)
        return
    environment = {
        **os.environ,
        "DPE_INSTALL_ROOT": str(package),
        "DPE_LAUNCHER": str(package / "launch.sh"),
        "DPE_PROFILE_DIR": str(profile),
        "DPE_APPLICATION": str(app),
        "DPE_APPLICATION_VERSION": read(app.parent / "application.json")["version"],
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
    }
    environment.pop("DPE_APPLICATION_READY", None)
    if token:
        environment["DPE_APPLICATION_READY"] = str(root / ("ready-" + token))
    arguments = args.args[1:] if args.args[:1] == ["--"] else args.args
    os.execve(app / "launch.sh", [str(app / "launch.sh"), *arguments], environment)


def show_error(error):
    print(f"Cannot start Discord Patched Electron: {error}", file=sys.stderr)
    if (
        "--launch" in sys.argv[1:]
        and shutil.which("zenity")
        and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    ):
        package = Path(__file__).resolve().parent
        try:
            channel = read(package / "bootstrap.json")["channel"]
        except FAILURES:
            channel = "canary"
        desktop = "discord" if channel == "stable" else "discord-" + channel
        identity = (os.environ.get("CHROME_DESKTOP") or desktop).removesuffix(".desktop")
        subprocess.run(
            [
                identity,
                "--window-icon=" + str(package / "discord.png"),
                "--error",
                "--no-markup",
                "--title=Discord Patched Electron",
                "--text=" + str(error)[-2000:],
            ],
            executable="zenity",
            check=False,
        )


if __name__ == "__main__":
    try:
        main()
    except FAILURES as error:
        show_error(error)
        raise SystemExit(1) from None
