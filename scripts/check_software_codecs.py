"""Check software video playback and encoding with the production codec loader."""

import argparse
import shutil
from pathlib import Path

from check_graphics import check
from common import read_json, sha256, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runtime", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--openh264", type=Path, required=True, help="Existing trusted OpenH264 library"
    )
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    # Exercise profile paths with spaces rather than relying on LD_PRELOAD's
    # whitespace-separated absolute path format.
    library = output / "codec cache" / args.openh264.name
    library.parent.mkdir()
    shutil.copy2(args.openh264, library)
    common = ["--enable-libopenh264", "--openh264-library-path=" + str(library)]
    software = ["--disable-accelerated-video-decode", "--disable-accelerated-video-encode"]
    cases = [
        ("default-h264", "h264", "wayland", True, True, []),
        *(
            ("software-" + codec, codec, "wayland", True, True, software)
            for codec in ("h264", "vp8", "vp9", "av1")
        ),
        ("software-h264-x11", "h264", "x11", True, True, software),
        ("control-no-preload", "h264", "wayland", False, False, software),
        ("software-hevc", "hevc", "wayland", False, True, software),
    ]
    results = []
    for name, codec, display, required, preload, extra in cases:
        try:
            report = check(
                args.runtime,
                output / name,
                ["--ozone-platform=" + display, *common, *extra],
                video_codec=codec,
                launcher_codecs=preload,
            )
        except Exception as error:
            path = output / name / "graphics.json"
            report = read_json(path) if path.exists() else {}
            report.update(passed=False, error=str(error))
        sandbox = report.get("rendererSandbox", {})
        passed = (
            report.get("passed") is True
            and report.get("codecPreferencesPassed") is True
            and sandbox.get("configured")
            and sandbox.get("seccomp") == 2
            and sandbox.get("noNewPrivs") == 1
        )
        results.append(
            {"case": name, "required": required, "passed": bool(passed), "report": report}
        )
        write_json(output / "software-codecs.json", {"results": results})
        print(name, bool(passed), flush=True)
    result = {
        "passed": all(row["passed"] for row in results if row["required"]),
        "runtime_sha256": sha256(args.runtime),
        "openh264_sha256": sha256(library),
        "results": results,
        "limits": "Synthetic video only. The no-preload control and hardware-only HEVC software attempt are diagnostic cases, not required passes.",
    }
    write_json(output / "software-codecs.json", result)
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
