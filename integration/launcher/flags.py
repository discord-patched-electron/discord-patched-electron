"""Resolve launch flags before Electron starts; also power the settings preview."""

import copy
import json
import os
import re
import shlex
import sys
from pathlib import Path

MAX_TEXT = 32768


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


def validate(config, data):
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
    try:
        lines(config.get("extra"), "Additional flags")
    except ValueError as error:
        raise InvalidFlags(error, "extra-flags") from error
    for key, profile in profiles.items():
        label = data["presets"][key]["label"]
        if not isinstance(profile, dict):
            raise ValueError(f"{label}: invalid preset settings.")
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
    settings = request.get("settings")
    if settings is None:
        settings = read_settings(profile)
    if "config" in request:
        config = validate(request["config"], data)
        warnings = []
    else:
        try:
            config, warnings = preferences(settings, data)
            validate(config, data)
        except ValueError as error:
            if not request.get("recoverInvalid"):
                raise
            config, warnings = (
                defaults(data),
                [str(error), "Showing defaults. Save to replace invalid launch preferences."],
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
        selected = config["profiles"][active]
        # Preset text is the final word within the preset; explicit feature
        # disables can override a convenience checkbox without duplicate switches.
        options = list(data["backends"][selected["backend"]]["flags"])
        for name in ("decode", "encode"):
            if selected[name]:
                options += data["video"][name]["flags"]
        for name in selected["experiments"]:
            options += data["experiments"][name]["flags"]
        add("Preset options", options)
        add(data["presets"][active]["label"] + " flags", lines(selected["flags"]))
        add("Additional flags", lines(config["extra"]))
    else:
        warnings.append(
            "DPE_DEFAULT_FLAGS=0 skips application defaults and all saved presets/options."
        )
    config_root = Path(
        env.get("XDG_CONFIG_HOME") or Path(env.get("HOME", str(Path.home()))) / ".config"
    )
    file_errors = []
    for name in (
        package + "-flags.conf",
        package.replace("-prerelease-patched-electron-bin", "-patched-electron") + ".conf",
    ):
        file = config_root / name
        try:
            content = file.read_text()
            file_flags = lines(content, str(file), editable=False)
        except FileNotFoundError:
            continue
        except (ValueError, OSError) as error:
            if not request.get("tolerateFileErrors"):
                raise
            file_errors.append(f"Fix {file} before restarting: {error}")
            continue
        add(str(file), file_flags)
    add("Command line", args)
    recovery = env.get("DPE_IGNORE_FLAGS") == "1" or "--ignore-flags" in args
    return {
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
        request = json.load(sys.stdin) if mode == "preview" else None
        if request is not None:
            request["tolerateFileErrors"] = True
            args = request.get("args", [])
        result = resolve(Path(profile), package, desktop, args, request=request)
        if mode == "preview":
            print(json.dumps(result))
        else:
            # The first NUL-delimited item preserves only the original arguments
            # for relaunch; resolved flags must not override a newly edited preset.
            sys.stdout.write(
                json.dumps(args) + "\0" + "".join(flag + "\0" for flag in result["effective"])
            )
            for warning in result["warnings"]:
                print("Launch flags: " + warning, file=sys.stderr)
    except (ValueError, OSError) as error:
        if mode == "preview":
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
