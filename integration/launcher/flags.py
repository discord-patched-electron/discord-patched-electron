"""Resolve launch flags before Electron starts; also power the settings preview."""

import copy
import fcntl
import hashlib
import json
import os
import re
import shlex
import stat
import sys
import tempfile
from pathlib import Path

MAX_TEXT = 32768
MAX_FILE = 1024 * 1024
BEGIN = "# >>> OpenAsar launch presets v1"
END = "# <<< OpenAsar launch presets v1"
META = "# presets: "


class InvalidFlags(ValueError):
    def __init__(self, error, field, preset=None):
        super().__init__(str(error))
        self.field, self.preset = field, preset


def catalog():
    return json.loads(Path(__file__).with_name("presets.json").read_text())


def feature_key(value):
    return re.split(r"[:<]", value.lstrip("*"), maxsplit=1)[0]


def lines(value, label="Flags", *, editable=True):
    if not isinstance(value, str) or len(value) > MAX_TEXT:
        raise ValueError(f"{label}: use at most {MAX_TEXT} characters.")
    result = []
    for number, raw in enumerate(value.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "\0" in line or not re.fullmatch(r"--[a-zA-Z0-9_-]+(?:=.*)?", line):
            raise ValueError(f"{label}, line {number}: use one --name or --name=value per line.")
        name, _, value = line.partition("=")
        if name == "--ignore-flags":
            raise ValueError(
                f"{label}: --ignore-flags is a recovery launch option, not a saved flag."
            )
        if name in ("--enable-features", "--disable-features"):
            features = value.split(",")
            if not all(feature.strip() and not re.search(r"\s", feature) for feature in features):
                raise ValueError(
                    f"{label}, line {number}: use comma-separated feature names without spaces."
                )
            if (
                editable
                and name == "--enable-features"
                and any(feature_key(feature) == "EnableDrDc" for feature in features)
            ):
                raise ValueError(
                    f"{label}: remove EnableDrDc; it is unsupported on Linux and crashes the GPU."
                )
        result.append(line)
    return result


def defaults(data):
    return {
        "schema": 1,
        "active": "balanced",
        "display": "auto",
        "extra": "",
        "profiles": {
            key: {
                "flags": item["flags"],
                "decode": False,
                "encode": False,
                "backend": "auto",
                "experiments": [],
            }
            for key, item in data["presets"].items()
        },
    }


def preferences(settings, data):
    oa = settings.get("openasar", {})
    if not isinstance(oa, dict):
        oa = {}
    result = defaults(data)
    saved = oa.get("launchFlags")
    warnings = []
    if saved is None:
        legacy_preset = oa.get("cmdPreset")
        if isinstance(legacy_preset, str):
            result["active"] = {"perf": "performance", "battery": "efficiency"}.get(
                legacy_preset, "balanced"
            )
        legacy = oa.get("customFlags", "")
        if isinstance(legacy, str) and legacy:
            imported = []
            for arg in shlex.split(legacy):
                if arg.startswith("--enable-features="):
                    names = [
                        v for v in arg.split("=", 1)[1].split(",") if feature_key(v) != "EnableDrDc"
                    ]
                    if names:
                        imported.append("--enable-features=" + ",".join(names))
                else:
                    imported.append(arg)
            result["extra"] = "\n".join(imported)
            warnings.append("Imported legacy custom flags. Review Additional flags before saving.")
        if oa.get("cmdPreset"):
            warnings.append(
                "Using the new preset definitions; old forced GPU tweaks were not migrated."
            )
        return result, warnings
    validate(saved, data)
    return saved, warnings


def validate(config, data, *, check_text=True):
    if not isinstance(config, dict) or config.get("schema") != 1:
        raise ValueError("Launch settings: unsupported format. Restore the preset defaults.")
    if (
        not isinstance(config.get("active"), str)
        or config["active"] not in data["presets"]
        or config.get("display")
        not in (
            "auto",
            "wayland",
            "x11",
        )
    ):
        raise ValueError("Choose a valid preset and display server.")
    profiles = config.get("profiles")
    if not isinstance(profiles, dict) or set(profiles) != set(data["presets"]):
        raise ValueError("Launch settings: one configuration is required for each preset.")
    if not isinstance(config.get("extra"), str):
        raise InvalidFlags("Additional flags must be text.", "extra-flags")
    if check_text:
        try:
            lines(config.get("extra"), "Additional flags")
        except ValueError as error:
            raise InvalidFlags(error, "extra-flags") from error
    for key, profile in profiles.items():
        label = data["presets"][key]["label"]
        if not isinstance(profile, dict):
            raise ValueError(f"{label}: invalid preset settings.")
        if not isinstance(profile.get("flags"), str):
            raise InvalidFlags(f"{label} flags must be text.", "preset-flags", key)
        if check_text:
            try:
                lines(profile.get("flags"), f"{label} flags")
            except ValueError as error:
                raise InvalidFlags(error, "preset-flags", key) from error
        if any(type(profile.get(name)) is not bool for name in ("decode", "encode")):
            raise ValueError(f"{label}: video options must be checked or unchecked.")
        if (
            not isinstance(profile.get("backend"), str)
            or profile["backend"] not in data["backends"]
        ):
            raise ValueError(f"{label}: choose a valid rendering backend.")
        options = profile.get("experiments")
        if not isinstance(options, list) or not all(
            isinstance(v, str)
            and (v in data["experiments"] or v in data.get("retiredExperiments", {}))
            for v in options
        ):
            raise ValueError(f"{label}: choose only listed experimental options.")
    return config


def file_paths(package, env):
    root = Path(env.get("XDG_CONFIG_HOME") or Path(env.get("HOME", str(Path.home()))) / ".config")
    return (
        root / (package.replace("-prerelease-patched-electron-bin", "-patched-electron") + ".conf"),
        root / (package + "-flags.conf"),
    )


def revision(target, raw):
    return hashlib.sha256(
        str(target).encode() + b"\0" + (b"missing" if raw is None else b"file\0" + raw)
    ).hexdigest()


def snapshot(path):
    target = path.resolve()
    try:
        with target.open("rb") as stream:
            raw = stream.read(MAX_FILE + 1)
        if len(raw) > MAX_FILE:
            raise ValueError(f"{path}: flags file must be at most {MAX_FILE} bytes.")
    except FileNotFoundError:
        raw = None
    return {"target": target, "raw": raw, "revision": revision(target, raw)}


def options(config, data):
    selected = config["profiles"][config["active"]]
    result = list(data["backends"][selected["backend"]]["flags"])
    for name in ("decode", "encode"):
        if selected[name]:
            result += data["video"][name]["flags"]
    for name in selected["experiments"]:
        if name in data["experiments"]:
            result += data["experiments"][name]["flags"]
    return result


def preset_body(config, data):
    text = "\n".join(
        [*options(config, data), config["profiles"][config["active"]]["flags"]]
    ).rstrip("\n")
    return text + "\n" if text else ""


def load_file(state, legacy, settings, data, *, recover=False):
    text = state["raw"].decode("utf-8") if state["raw"] is not None else ""
    rows = text.splitlines(keepends=True)
    starts = [i for i, row in enumerate(rows) if row.strip() == BEGIN]
    ends = [i for i, row in enumerate(rows) if row.strip() == END]
    if not starts and not ends:
        try:
            config, warnings = preferences(settings, data)
        except ValueError as error:
            if not recover:
                raise
            config, warnings = (
                defaults(data),
                [str(error), "Showing defaults. Save to replace invalid launch preferences."],
            )
        config = copy.deepcopy(config)
        old = snapshot(legacy)["raw"]
        old = old.decode("utf-8") if old is not None else ""
        # Preserve the old precedence once, then use only this canonical file.
        parts = [config["extra"], old, text]
        config["extra"] = "\n".join(part.rstrip("\n") for part in parts if part)
        if parts[-1].endswith("\n"):
            config["extra"] += "\n"
        return config, warnings, False
    outside, body = "", ""
    try:
        if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
            raise ValueError("invalid preset section markers")
        start, end = starts[0], ends[0]
        outside = "".join(rows[:start] + rows[end + 1 :])
        body = "".join(rows[start + 1 : end])
        if not rows[start + 1].startswith(META):
            raise ValueError("missing preset metadata")
        body = "".join(rows[start + 2 : end])
        config = json.loads(rows[start + 1][len(META) :])
        if not isinstance(config, dict):
            raise ValueError("preset metadata must be an object")
        config["extra"] = outside
        validate(config, data, check_text=False)
        warnings = []
        if body != preset_body(config, data):
            # The editable active lines win over the UI's remembered controls.
            # Keep inactive presets and global file overrides intact.
            config["profiles"][config["active"]].update(
                flags=body, decode=False, encode=False, backend="auto", experiments=[]
            )
            warnings.append(
                "Active flags were edited in the file. Loaded them as preset text and reset its option controls."
            )
        return config, warnings, True
    except (ValueError, KeyError, TypeError, IndexError) as error:
        if not recover:
            raise ValueError(
                f"Invalid flags-file preset data: {error}. Start with --ignore-flags to repair it."
            ) from error
        config = defaults(data)
        # Keep raw manual flags available for repair even with damaged metadata.
        recovered = body + outside if body or outside else text
        config["extra"] = "".join(
            "# preserved: " + row if row.strip() in (BEGIN, END) or row.startswith(META) else row
            for row in recovered.splitlines(keepends=True)
        )
        return (
            config,
            [
                f"Invalid preset metadata: {error}. Showing file flags for repair; Save backs up the original file."
            ],
            False,
        )


def render_file(config, data):
    for key, text in [
        ("extra-flags", config["extra"]),
        *[(name, p["flags"]) for name, p in config["profiles"].items()],
    ]:
        if any(row.strip() in (BEGIN, END) or row.startswith(META) for row in text.splitlines()):
            raise InvalidFlags(
                "Remove reserved OpenAsar section markers from editable flags.",
                "extra-flags" if key == "extra-flags" else "preset-flags",
                None if key == "extra-flags" else key,
            )
    metadata = {key: value for key, value in config.items() if key != "extra"}
    result = (
        BEGIN
        + "\n"
        + META
        + json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
        + "\n"
        + preset_body(config, data)
        + END
        + "\n"
        + config["extra"]
    )
    if len(result.encode()) > MAX_FILE:
        raise ValueError("Launch settings exceed the maximum flags-file size.")
    return result


def save_file(result, expected_revision):
    path = Path(result["flagsFile"])
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    # Cooperating app instances serialize writes; the revision also detects
    # changes made by an ordinary editor while our window was open.
    lock_fd = os.open(str(target) + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(lock_fd, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = snapshot(path)
        if current["revision"] != expected_revision:
            raise ValueError(
                "Flags file changed on disk. Reload from file to review it before saving."
            )
        text = render_file(result["config"], result["catalog"])
        backup = None
        if current["raw"] is not None and not result["fileManaged"]:
            fd, backup = tempfile.mkstemp(prefix=target.name + ".backup-", dir=target.parent)
            with os.fdopen(fd, "wb") as stream:
                stream.write(current["raw"])
                stream.flush()
                os.fsync(stream.fileno())
        fd, temporary = tempfile.mkstemp(prefix=target.name + ".tmp-", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                if current["raw"] is not None:
                    os.fchmod(stream.fileno(), stat.S_IMODE(target.stat().st_mode))
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            if snapshot(path)["revision"] != expected_revision:
                raise ValueError("Flags file changed on disk. Reload from file before saving.")
            os.replace(temporary, target)
            saved_revision = revision(target, text.encode("utf-8"))
        finally:
            Path(temporary).unlink(missing_ok=True)
    result.update(fileRevision=saved_revision, fileManaged=True, needsMigration=False)
    if backup:
        result["warnings"].append(f"Previous flags file backed up to {backup}.")
    return result


def merge(layers):
    ordinary, features = [], {}
    for layer in layers:
        for flag in layer["flags"]:
            name, _, value = flag.partition("=")
            if name in ("--enable-features", "--disable-features"):
                for feature in value.split(","):
                    if feature:
                        features[feature_key(feature)] = (name, feature)
            else:
                ordinary.append(flag)
    for name in ("--enable-features", "--disable-features"):
        values = [value for state, value in features.values() if state == name]
        if values:
            ordinary.append(name + "=" + ",".join(values))
    return ordinary


def read_settings(profile):
    try:
        value = json.loads((profile / "settings.json").read_text())
        return value if isinstance(value, dict) else {}
    except FileNotFoundError:
        return {}
    except (ValueError, OSError) as error:
        raise ValueError(
            "Could not read settings.json. Start with --ignore-flags to repair launch settings."
        ) from error


def resolve(profile, package, desktop, args, env=None, request=None):
    env = os.environ if env is None else env
    request = request or {}
    data = catalog()
    file, legacy = file_paths(package, env)
    state = snapshot(file)
    settings = request.get("settings")
    if settings is None:
        settings = read_settings(profile)
    file_managed = False
    if "config" in request:
        config = validate(request["config"], data)
        warnings = []
        # Determine whether an existing file needs a migration/repair backup,
        # without making a valid replacement depend on damaged old settings.
        _, _, file_managed = load_file(state, legacy, {}, data, recover=True)
    else:
        config, warnings, file_managed = load_file(
            state, legacy, settings, data, recover=request.get("recoverInvalid", False)
        )
    # Retire ineffective convenience controls without rejecting saved settings
    # or replacing them with stronger overrides the user never selected.
    config = copy.deepcopy(config)
    for key, selected in config["profiles"].items():
        for name in selected["experiments"]:
            if reason := data.get("retiredExperiments", {}).get(name):
                warnings.append(f"{data['presets'][key]['label']}: {reason}")
        selected["experiments"] = [
            name for name in selected["experiments"] if name in data["experiments"]
        ]
    layers = []

    def add(name, flags):
        layers.append({"name": name, "flags": flags})

    add("Application identity", ["--class=" + desktop])
    if env.get("DPE_DEFAULT_FLAGS") != "0":
        display = config["display"]
        if display == "auto":
            display = "wayland" if env.get("WAYLAND_DISPLAY") else "x11"
        common = ["--ozone-platform=" + display, "--autoplay-policy=no-user-gesture-required"]
        if settings.get("openH264Enabled", True):
            common += [
                "--enable-libopenh264",
                "--openh264-library-path="
                + str(profile / "discord_asset_cache/openh264/libopenh264-2.5.1-linux64.7.so"),
            ]
        add("Application defaults", common)
        active = config["active"]
        # Preset text is the final word within the preset; explicit feature
        # disables can override a convenience checkbox without duplicate switches.
        add("Preset options", options(config, data))
    else:
        warnings.append(
            "DPE_DEFAULT_FLAGS=0 skips application defaults and all saved presets/options."
        )
    file_errors = []
    # Additional flags are the plain lines outside the managed preset section.
    # File edits remain literal, including explicitly requested unsafe flags.
    texts = [("Additional flags", config["extra"], "extra-flags", None)]
    if env.get("DPE_DEFAULT_FLAGS") != "0":
        active = config["active"]
        texts.insert(
            0,
            (
                data["presets"][active]["label"] + " flags",
                config["profiles"][active]["flags"],
                "preset-flags",
                active,
            ),
        )
    for label, content, field, preset in texts:
        try:
            add(label, lines(content, label, editable="config" in request))
        except ValueError as error:
            if not request.get("tolerateFileErrors"):
                raise InvalidFlags(error, field, preset) from error
            file_errors.append(f"Fix {file} before restarting: {error}")
    add("Command line", args)
    recovery = env.get("DPE_IGNORE_FLAGS") == "1" or "--ignore-flags" in args
    return {
        "flagsFile": str(file),
        "fileRevision": state["revision"],
        "fileManaged": file_managed,
        "needsMigration": not file_managed,
        "catalog": data,
        "config": config,
        "defaults": defaults(data),
        "warnings": warnings,
        "fileErrors": file_errors,
        "layers": layers,
        "effective": [] if recovery else merge(layers),
        "normalEffective": merge(layers),
        "recovery": recovery,
        "originalArgs": args,
    }


def main():
    mode, profile, package, desktop, *args = sys.argv[1:]
    if args[:1] == ["--"]:
        args = args[1:]
    try:
        request = json.load(sys.stdin) if mode in ("preview", "save") else None
        if request is not None:
            request["tolerateFileErrors"] = True
            args = request.get("args", [])
        result = resolve(Path(profile), package, desktop, args, request=request)
        if mode == "save":
            if "config" not in request or not isinstance(request.get("fileRevision"), str):
                raise ValueError("Reload from file before saving launch settings.")
            save_file(result, request["fileRevision"])
        if mode in ("preview", "save"):
            print(json.dumps(result))
        else:
            if result["needsMigration"]:
                try:
                    save_file(result, result["fileRevision"])
                except (OSError, ValueError) as error:
                    result["warnings"].append(
                        f"Could not migrate launch flags to {result['flagsFile']}: {error}"
                    )
            # The first NUL-delimited item preserves only the original arguments
            # for relaunch; resolved flags must not override a newly edited preset.
            sys.stdout.write(
                json.dumps(args) + "\0" + "".join(flag + "\0" for flag in result["effective"])
            )
            for warning in result["warnings"]:
                print("Launch flags: " + warning, file=sys.stderr)
    except (ValueError, OSError) as error:
        if mode in ("preview", "save"):
            details = (
                {"field": error.field, "preset": error.preset}
                if isinstance(error, InvalidFlags)
                else {}
            )
            print(json.dumps({"error": str(error), **details}))
        else:
            print(f"Launch flags: {error}\nStart with --ignore-flags to recover.", file=sys.stderr)
            raise SystemExit(1) from error


if __name__ == "__main__":
    main()
