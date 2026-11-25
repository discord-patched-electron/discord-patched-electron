"""Stable, stdlib-only trust boundary. Application code lives in signed generations."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import http.client
import json
import os
import re
import select as io_select
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from contextlib import nullcontext
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


_diagnostic_profile = None
SPLASH_CANCELLED = 42
SPLASH_RETRY = 43


class SetupCancelled(Exception):
    """The user closed the Electron startup screen."""


class SetupRetry(Exception):
    """The user requested another setup attempt from the startup screen."""


def cached_splash_runtime(profile, policy, applications):
    pinned = policy.get("runtime")
    pins = [(pinned, None)] if pinned else []
    for application in applications:
        try:
            pins.append((read(application / "runtime-update.json"), application))
        except FAILURES:
            continue
    for pin, application in pins:
        if not isinstance(pin, dict) or any(
            pin.get(key) != policy[key] for key in ("channel", "electron_channel")
        ):
            continue
        digest = pin.get("archive_sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            continue
        for directory in (profile / "dpe-runtime").glob("*/generations/" + digest):
            try:
                metadata = read(directory / "runtime.json")
                if any(
                    metadata[key] != pin[key]
                    for key in (
                        "archive_sha256",
                        "runtime_key",
                        "electron_version",
                        "channel",
                        "electron_channel",
                    )
                ):
                    continue
                if (
                    application is not None
                    and (directory / "resources").resolve() != application / "resources"
                ):
                    continue
                files = pin["files"] if application is None else metadata["files"]
                if application is not None and files["electron"] != metadata["executable_sha256"]:
                    continue
                actual = {
                    item.relative_to(directory).as_posix(): checksum(item)
                    for item in directory.rglob("*")
                    if item.is_file()
                    and item.name != "runtime.json"
                    and item.relative_to(directory).parts[0] != "resources"
                }
                if files == actual and os.access(directory / "electron", os.X_OK):
                    return directory
            except FAILURES:
                continue
    return None


class SetupProgress:
    def __init__(self, package, profile, policy):
        self.package = package
        self.profile = profile
        self.policy = policy
        channel = policy["channel"]
        self.desktop = "discord" if channel == "stable" else "discord-" + channel
        self.dialog = None
        self.temporary = None
        self.attempted = False
        self.ready = False

    def __enter__(self):
        return self

    def show(self, applications):
        if self.attempted:
            return
        self.attempted = True
        runtime = cached_splash_runtime(self.profile, self.policy, applications)
        if runtime is None:
            return
        try:
            self.temporary = tempfile.TemporaryDirectory(prefix="splash-", dir=self.profile)
            launch = Path(self.temporary.name)
            # A separate executable path gives the splash its own resources
            # without changing the runtime used by a running Discord process.
            os.link(runtime / "electron", launch / "electron")
            for entry in runtime.iterdir():
                if entry.name not in ("resources", "electron"):
                    (launch / entry.name).symlink_to(entry)
            resources = launch / "resources"
            resources.mkdir()
            (resources / "app").symlink_to(self.package / "splash")
            command = [str(launch / "electron"), "--disable-setuid-sandbox"]
            name = f"{self.desktop}-{self.policy['electron_channel']}-patched-electron-bin"
            if Path("/etc/apparmor.d", name).is_file() and shutil.which("aa-exec"):
                if (
                    subprocess.run(
                        ["aa-exec", "-p", name, "--", "true"], capture_output=True
                    ).returncode
                    == 0
                ):
                    command[:0] = ["aa-exec", "-p", name, "--"]
            environment = {
                **os.environ,
                "DPE_APP_ROOT": str(self.package),
                "DPE_PROFILE_DIR": str(self.profile),
                "DPE_SPLASH_USER_DATA": str(launch / "user-data"),
                "DPE_CLIENT_OFFLINE": "1",
                "CHROME_DESKTOP": os.environ.get("CHROME_DESKTOP") or self.desktop + ".desktop",
            }
            for variable in ("ELECTRON_RUN_AS_NODE", "NODE_OPTIONS", "LD_PRELOAD"):
                environment.pop(variable, None)
            self.dialog = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                bufsize=0,
                start_new_session=True,
                env=environment,
            )
            # Start update I/O after the window paints. A display service or
            # splash runtime failure must not block package recovery.
            self.wait_for_window(1)
            self.check()
        except OSError as error:
            diagnostic("bootstrap-splash-unavailable", error=str(error))

    def wait_for_window(self, timeout):
        if (
            self.dialog
            and not self.ready
            and io_select.select([self.dialog.stdout], [], [], timeout)[0]
        ):
            self.ready = self.dialog.stdout.readline() == b"ready\n"

    def check(self):
        if self.dialog and self.dialog.poll() == SPLASH_CANCELLED:
            raise SetupCancelled

    def installing(self):
        self.send({"stage": "install"})

    def send(self, message):
        self.check()
        if self.dialog and self.dialog.poll() is None:
            try:
                self.dialog.stdin.write((json.dumps(message) + "\n").encode())
                self.dialog.stdin.flush()
                return True
            except BrokenPipeError:
                self.check()
        return False

    def __exit__(self, error_type, error, _traceback):
        try:
            if isinstance(error, FAILURES):
                self.show([])
                self.wait_for_window(4)
                self.check()
                if self.ready and self.send({"stage": "error", "details": str(error)[-4000:]}):
                    status = self.dialog.wait()
                    if status == SPLASH_RETRY:
                        raise SetupRetry
                    if status == SPLASH_CANCELLED:
                        return
                if self.dialog is None or self.dialog.poll() is not None:
                    native_error(self.package, self.desktop, error)
        finally:
            self.close(error_type is None)

    def close(self, check_cancelled):
        try:
            if self.dialog:
                self.dialog.stdin.close()
                self.dialog.stdout.close()
                try:
                    status = self.dialog.wait(timeout=1)
                    if check_cancelled and status == SPLASH_CANCELLED:
                        raise SetupCancelled
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(self.dialog.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    self.dialog.wait()
        finally:
            if self.temporary:
                self.temporary.cleanup()


def no_cancel():
    pass


def diagnostic(stage, **details):
    if _diagnostic_profile is None:
        return
    try:
        directory = _diagnostic_profile / "logs/dpe"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        file = directory / "bootstrap-loader.log"
        if file.exists() and file.lstat().st_size > 2 * 1024 * 1024:
            file.replace(file.with_name(file.name + ".1"))
        fd = os.open(
            file, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600
        )
        try:
            if stat.S_ISREG(os.fstat(fd).st_mode):
                os.write(
                    fd,
                    (json.dumps({"time": time.time(), "stage": stage, **details}) + "\n").encode()[
                        :16384
                    ],
                )
        finally:
            os.close(fd)
    except OSError:
        pass


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


def extract(archive, app, *, check_cancelled=no_cancel):
    total, seen = 0, set()
    with zipfile.ZipFile(archive) as source:
        if len(source.infolist()) > 20000:
            raise ValueError("Application archive has too many entries")
        for item in source.infolist():
            check_cancelled()
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


def install(root, package, policy, candidate, archive, proof=None, *, check_cancelled=no_cancel):
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
        extract(archive, staged / "app", check_cancelled=check_cancelled)
        validate(staged, candidate)
        check_cancelled()
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


def download(url, target, *, check_cancelled=no_cancel):
    if not url.startswith(f"https://github.com/{REPOSITORY}/releases/download/"):
        raise ValueError("Unexpected application download URL")
    opener = urllib.request.build_opener(HTTPSRedirect())
    for attempt in range(3):
        complete = False
        try:
            check_cancelled()
            with opener.open(url, timeout=20) as response, target.open("wb") as output:
                length = response.headers.get("Content-Length")
                expected = int(length) if length is not None else None
                if expected is not None and not 0 <= expected <= 64 * 1024 * 1024:
                    raise ValueError("Application download exceeds size limit")
                total = 0
                while block := response.read(1024 * 1024):
                    check_cancelled()
                    total += len(block)
                    if total > 64 * 1024 * 1024:
                        raise ValueError("Application download exceeds size limit")
                    output.write(block)
                if expected is not None and total < expected:
                    raise http.client.IncompleteRead(b"", expected - total)
            check_cancelled()
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
                and (
                    error.code not in (408, 500, 502, 503, 504)
                    or (error.headers and error.headers.get("Retry-After"))
                )
            ):
                raise
            print(f"Application download interrupted; retrying: {error}", file=sys.stderr)
        finally:
            if not complete:
                target.unlink(missing_ok=True)
        time.sleep(attempt + 1)


def cache_root(package, profile):
    launcher = installed_launcher(package.resolve())
    identity_path = (
        launcher if launcher != str(package.resolve() / "launch.sh") else str(package.resolve())
    )
    identity = hashlib.sha256(identity_path.encode()).hexdigest()[:16]
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
        references = [state.get(key) for key in ("current", "previous", "pending", "baseline")]
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


def select(root, package, policy, *, archive=None, launch=False, progress=None):
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
            if (root / ("started-" + trial["token"])).is_file():
                state.setdefault("rejected", []).append(trial["digest"])
            else:
                # Setup may be cancelled or fail offline before Discord starts.
                # Keep the verified update available, without replacing a newer one.
                state.setdefault("pending", trial["digest"])
            state.pop("trial")
        write(state_file, state)
    baseline = policy["application"]
    baseline_changed = state.get("baseline") != baseline["archive_sha256"]
    if baseline_changed:
        # A changed package pin permits one fresh trial, including when migrating
        # state from an older loader. The same pin must not revive every crash.
        state["baseline"] = baseline["archive_sha256"]
        state["rejected"] = [
            digest for digest in state.get("rejected", []) if digest != state["baseline"]
        ]
        write(state_file, state)
    running = next(
        (record for record in live_instances(state) if record["pid"] != os.getpid()), None
    )
    if launch and running:
        # Forwarders and independent instances must both retain the live code.
        app, _ = generation(root, running["digest"], package, policy)
        record_launch(root, state, running["digest"])
        return app, None
    check_cancelled = progress.check if progress else no_cancel
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
            check_cancelled()
            with tempfile.TemporaryDirectory(prefix="download-", dir=root) as temporary:
                source = archive
                if source is None:
                    if os.environ.get("DPE_CLIENT_OFFLINE") == "1":
                        raise ValueError(
                            "Launch once with internet access to download Discord Patched Electron"
                        )
                    print("Downloading Discord Patched Electron...", file=sys.stderr)
                    source = Path(temporary) / "application.zip"
                if progress:
                    progress.show([app for _, _, app in sorted(valid, reverse=True)])
                if archive is None:
                    download(policy["url"], source, check_cancelled=check_cancelled)
                if progress:
                    progress.installing()
                directory = install(
                    root, package, policy, baseline, source, check_cancelled=check_cancelled
                )
                valid.append((baseline["version"], baseline["archive_sha256"], directory / "app"))
        except FAILURES:
            if not valid:
                raise
    if not valid:
        raise ValueError("No working application is available; reinstall or retry online")
    check_cancelled()
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


def installed_launcher(package):
    # AppImage mounts/extraction directories disappear after exit. Persist the
    # outer executable for relaunch and autostart, but only for our AppDir layout.
    appdir, image = os.environ.get("APPDIR"), os.environ.get("APPIMAGE")
    if appdir and image and package == Path(appdir).resolve() / "usr/lib/dpe":
        executable = Path(image)
        if executable.is_absolute() and executable.is_file() and os.access(executable, os.X_OK):
            return str(executable)
    return str(package / "launch.sh")


def main():
    global _diagnostic_profile
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
    _diagnostic_profile = profile
    diagnostic("select-application")
    root = cache_root(package, profile)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if args.stage:
        with (root / ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
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
    while True:
        visibility = (
            SetupProgress(package, profile, policy)
            if args.launch
            and (package / "splash/package.json").is_file()
            and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
            else nullcontext()
        )
        try:
            with visibility as progress, (root / ".lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                app, token = select(
                    root,
                    package,
                    policy,
                    archive=args.archive,
                    launch=args.launch,
                    progress=progress,
                )
            break
        except SetupRetry:
            continue
    if not args.launch:
        print(app)
        return
    diagnostic("launch-application", application=str(app))
    environment = {
        **os.environ,
        "DPE_INSTALL_ROOT": str(package),
        "DPE_LAUNCHER": installed_launcher(package),
        "DPE_PROFILE_DIR": str(profile),
        "DPE_APPLICATION": str(app),
        "DPE_APPLICATION_VERSION": read(app.parent / "application.json")["version"],
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
    }
    environment.pop("DPE_APPLICATION_READY", None)
    environment.pop("DPE_APPLICATION_STARTED", None)
    if token:
        environment["DPE_APPLICATION_READY"] = str(root / ("ready-" + token))
        environment["DPE_APPLICATION_STARTED"] = str(root / ("started-" + token))
    arguments = args.args[1:] if args.args[:1] == ["--"] else args.args
    os.execve(app / "launch.sh", [str(app / "launch.sh"), *arguments], environment)


def native_error(package, desktop, error):
    # Called only after the Electron startup screen could not be displayed.
    if shutil.which("zenity"):
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
    except SetupCancelled:
        diagnostic("bootstrap-cancelled")
        raise SystemExit(1) from None
    except FAILURES as error:
        diagnostic("bootstrap-error", error=str(error))
        print(f"Cannot start Discord Patched Electron: {error}", file=sys.stderr)
        raise SystemExit(1) from None
