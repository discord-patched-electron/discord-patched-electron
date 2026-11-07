"""Verify native-sized detail and centred VAAPI padding with generated pixels.

GPU encode and software decode only: no desktop, account or physical camera.
The readback is confined to this test, never the running capture path.
"""

import argparse
import json
import subprocess
from pathlib import Path

import numpy as np
from common import ROOT, sha256, write_json


def check(backend, node, output):
    if output.exists():
        raise ValueError("Choose a new validation directory")
    output.mkdir(parents=True)
    binary = output / "padding-check"
    subprocess.run(
        [
            "cc",
            "-std=c11",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-I" + str(ROOT / "integration/video"),
            str(ROOT / "tests/screenshare/vaapi-padding-check.c"),
            "-ldl",
            "-o",
            str(binary),
        ],
        check=True,
    )
    cases = []
    with (output / "encode.log").open("w") as log:
        for codec in (1, 2):
            for width, height in (
                (256, 256),
                (640, 360),
                (1280, 712),
                (1280, 720),
                (1920, 1080),
                (1918, 1078),
            ):
                file = output / f"{codec}-{width}x{height}.{'ivf' if codec == 2 else '265'}"
                encoded = subprocess.run(
                    [
                        str(binary),
                        str(backend.resolve()),
                        str(node),
                        str(file),
                        str(width),
                        str(height),
                        str(codec),
                    ],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=log,
                    text=True,
                    timeout=20,
                )
                amd = encoded.stdout.strip() == "1"
                coded_width = (width + 63) & ~63 if codec == 2 and amd else width
                coded_height = (height + 15) & ~15 if codec == 2 and amd else height
                probe = subprocess.run(
                    [
                        "ffprobe",
                        "-v",
                        "error",
                        "-show_entries",
                        "stream=width,height",
                        "-of",
                        "json",
                        str(file),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                stream = json.loads(probe.stdout)["streams"][0]
                native_short_padding = (
                    codec == 2
                    and amd
                    and width % 64 == 0
                    and height % 16 == 8
                    and (stream["width"], stream["height"]) == (width, height + 2)
                )
                if native_short_padding:
                    coded_height = height + 2
                left, top = ((coded_width - width) // 2) & ~1, ((coded_height - height) // 2) & ~1
                if (stream["width"], stream["height"]) != (coded_width, coded_height):
                    raise ValueError("Unexpected decoded geometry")
                decoded = subprocess.run(
                    [
                        "ffmpeg",
                        "-v",
                        "error",
                        "-i",
                        str(file),
                        "-frames:v",
                        "1",
                        "-pix_fmt",
                        "yuv420p",
                        "-f",
                        "rawvideo",
                        "-",
                    ],
                    check=True,
                    capture_output=True,
                    timeout=20,
                )
                plane_size = coded_height * coded_width
                planes = np.frombuffer(decoded.stdout, dtype=np.uint8)
                image = planes[:plane_size].reshape(coded_height, coded_width)
                u = planes[plane_size : plane_size * 5 // 4].reshape(
                    coded_height // 2, coded_width // 2
                )
                v = planes[plane_size * 5 // 4 :].reshape(coded_height // 2, coded_width // 2)
                # Compare decoded YUV directly, including neutral chroma in the
                # margins. Luma alone cannot detect a green NV12 border.
                y, x = np.indices((height, width))
                source = np.full((height, width), 80, dtype=np.float64)
                source[(x >= 32) & (x < 96)] = np.where(x & 1, 170, 60)[(x >= 32) & (x < 96)]
                source[(y >= 32) & (y < 96)] = np.where(y & 1, 170, 60)[(y >= 32) & (y < 96)]
                source[(x < 4) | (y < 4) | (x >= width - 4) | (y >= height - 4)] = 220
                expected = source
                error = np.abs(
                    image[top : top + height, left : left + width].astype(float) - expected
                )
                padding = np.ones(image.shape, dtype=bool)
                padding[top : top + height, left : left + width] = False
                pad_error = (
                    # Firmware black may have Y below 16; it still displays as
                    # black. Require neutral chroma and no brighter margin.
                    int(np.maximum(image[padding].astype(int) - 16, 0).max())
                    if padding.any()
                    else 0
                )
                chroma_padding = np.ones(u.shape, dtype=bool)
                chroma_padding[top // 2 : (top + height) // 2, left // 2 : (left + width) // 2] = (
                    False
                )
                chroma_error = (
                    max(int(np.abs(p[chroma_padding].astype(int) - 128).max()) for p in (u, v))
                    if chroma_padding.any()
                    else 0
                )
                result = {
                    "codec": "AV1" if codec == 2 else "HEVC",
                    "source": [width, height],
                    "encoded": [coded_width, coded_height],
                    "offset": [left, top],
                    "native_short_padding": native_short_padding,
                    "mean_luma_error": float(error.mean()),
                    "detail_error_fraction": float((error > 20).mean()),
                    "padding_luma_error": pad_error,
                    "padding_chroma_error": chroma_error,
                }
                result["passed"] = (
                    result["mean_luma_error"] < 4
                    and result["detail_error_fraction"] < 0.005
                    and pad_error < 16
                    and chroma_error < 16
                )
                cases.append(result)
    report = {
        "passed": all(c["passed"] for c in cases),
        "backend_sha256": sha256(backend),
        "render_node": str(node),
        "cases": cases,
    }
    write_json(output / "report.json", report)
    if not report["passed"]:
        raise ValueError("Native image detail or padding verification failed; see report.json")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", required=True, type=Path)
    parser.add_argument("--render-node", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(check(args.backend, args.render_node, args.output), indent=2))
