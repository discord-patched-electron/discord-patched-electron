"""Probe resolved presets and opt-in experiments on the current GPU, without Discord."""

import argparse
import importlib.util
import os
import shutil
from pathlib import Path

from check_graphics import VIDEO_CODECS, check
from common import ROOT, read_json, sha256, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runtime", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--openh264", type=Path, help="Existing OpenAsar libopenh264-2.5.1-linux64.7.so"
    )
    parser.add_argument(
        "--full", action="store_true", help="Also exercise each experimental option separately"
    )
    parser.add_argument(
        "--codecs", action="store_true", help="Also play VP8, VP9, AV1 and HEVC clips"
    )
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    spec = importlib.util.spec_from_file_location(
        "launch_flags", ROOT / "integration/launcher/flags.py"
    )
    resolver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(resolver)
    data = resolver.catalog()
    profile = output / "preferences"
    (output / "config").mkdir()
    settings = {"openH264Enabled": bool(args.openh264)}
    if args.openh264:
        library = profile / "discord_asset_cache/openh264/libopenh264-2.5.1-linux64.7.so"
        library.parent.mkdir(parents=True)
        shutil.copy2(args.openh264, library)
    env = {
        **os.environ,
        "XDG_CONFIG_HOME": str(output / "config"),
        "DPE_DEFAULT_FLAGS": "1",
        "DPE_IGNORE_FLAGS": "0",
    }
    cases = []
    for preset in data["presets"]:
        for display in ("wayland", "x11"):
            config = resolver.defaults(data)
            config.update(active=preset, display=display)
            cases.append((preset + "-" + display, config, True, "h264"))
    for decode, encode in ((True, False), (False, True), (True, True)):
        config = resolver.defaults(data)
        config["display"] = "wayland"
        config["profiles"]["balanced"].update(decode=decode, encode=encode)
        name = "video-" + ("both" if decode and encode else "decode" if decode else "encode")
        cases.append((name, config, False, "h264"))
    recovery_config = resolver.defaults(data)
    recovery_config["profiles"]["balanced"].update(
        decode=True, encode=True, experiments=["graphiteForce"]
    )
    cases.append(("ignore-flags", recovery_config, True, "h264"))
    if args.codecs:
        for codec in VIDEO_CODECS:
            if codec == "h264":
                continue
            config = resolver.defaults(data)
            config["display"] = "wayland"
            cases.append(("codec-" + codec, config, True, codec))
    if args.full:
        for group, keys in (
            ("backends", ["gl", "vulkan"]),
            ("experiments", data["experiments"]),
        ):
            for key in keys:
                config = resolver.defaults(data)
                config["display"] = "wayland"
                selected = config["profiles"]["balanced"]
                if group == "backends":
                    selected["backend"] = key
                else:
                    selected["experiments"] = [key]
                cases.append((group + "-" + key, config, False, "h264"))
    results = []
    for name, config, required, codec in cases:
        draft = resolver.resolve(
            profile,
            "discord-canary-prerelease-patched-electron-bin",
            "discord-canary",
            [],
            env=env,
            request={"settings": settings, "config": config},
        )
        resolver.save_file(draft, draft["fileRevision"])
        # Test the launcher's actual file reader, not just the in-memory draft.
        resolved = resolver.resolve(
            profile,
            "discord-canary-prerelease-patched-electron-bin",
            "discord-canary",
            [],
            env={**env, "DPE_IGNORE_FLAGS": "1" if name == "ignore-flags" else "0"},
            request={"settings": settings},
        )
        flags = resolved["effective"]
        try:
            report = check(
                args.runtime, output / name, flags, video_codec=codec, launcher_codecs=True
            )
        except Exception as error:
            report_path = output / name / "graphics.json"
            report = read_json(report_path) if report_path.exists() else {}
            report.update(passed=False, error=str(error))
        results.append(
            {
                "case": name,
                "required": required,
                "passed": report.get("passed", False),
                "gpuExits": report.get("gpuExits"),
                "error": report.get("error"),
                "flags": flags,
                "config": config,
                "video_codec": codec,
                "file_revision": resolved["fileRevision"],
                "players": report.get("players"),
                "encoders": report.get("rendering", {}).get("encoders"),
                "decoders": report.get("rendering", {}).get("decoders"),
                "codecPreferencesPassed": report.get("codecPreferencesPassed"),
                "codecPreferenceFailures": report.get("codecPreferenceFailures"),
            }
        )
        print(name, results[-1]["passed"], flush=True)
        write_json(output / "matrix.json", {"results": results})
    result = {
        "passed": all(row["passed"] for row in results if row["required"]),
        "runtime_sha256": sha256(args.runtime),
        "openh264_sha256": sha256(args.openh264) if args.openh264 else None,
        "catalog": data,
        "results": results,
        "limits": "Synthetic local video, WebGL and WebCodecs on this GPU only. Experimental failures are recorded, not accepted as safe defaults. No calls or physical camera use.",
    }
    write_json(output / "matrix.json", result)
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
