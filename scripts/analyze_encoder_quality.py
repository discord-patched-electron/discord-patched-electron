"""Measure sampled RGB error against the private GPU fixture's exact source.

Only owned synthetic scroll/scroll16 recordings and their source stamp/sequence
map are accepted. This is a compression comparison, not a perceptual score or
live-call latency measurement. No camera, account data or desktop is read.
"""

import argparse
import json
import math
import re
import subprocess
from pathlib import Path

from extract_screenshare_av1 import extract as extract_av1
from extract_screenshare_h264 import extract as extract_h264
from extract_screenshare_hevc import extract as extract_hevc


def hash32(value):
    value = ((value ^ (value >> 16)) * 0x7FEB352D) & 0xFFFFFFFF
    value = ((value ^ (value >> 15)) * 0x846CA68B) & 0xFFFFFFFF
    return value ^ (value >> 16)


def marker(data, width, height):
    values = [0] * 5
    for bit in range(40):
        x = int((bit + 0.5) * width / 40)
        levels = []
        for row in range(2):
            y = int((row + 0.5) * height * 0.06)
            offset = (y * width + x) * 3
            levels.append(sum(data[offset : offset + 3]) / 3)
        a, b = levels
        if not ((a < 80 and b > 175) or (a > 175 and b < 80)):
            return None
        values[bit // 8] = (values[bit // 8] << 1) | int(a > b)
    crc = 0
    for byte in values[:4]:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ (7 if crc & 128 else 0)) & 255
    if values[0] != 0xA7 or values[4] != crc:
        return None
    return (values[1] << 16) | (values[2] << 8) | values[3]


def analyze(directory):
    config = json.loads((directory / "config.json").read_text())
    if config.get("content_pattern") not in ("scroll", "scroll16"):
        raise ValueError("Quality comparison requires the synthetic scrolling source")
    # Extractors enforce explicit recording, codec, key and recording bounds.
    extractors = {"h264-vaapi": extract_h264, "h265-vaapi": extract_hevc, "av1-vaapi": extract_av1}
    if config.get("encoder") not in extractors:
        raise ValueError("Unsupported synthetic encoder")
    encoded = extractors[config["encoder"]](directory)
    source_log = directory / "source.log"
    if source_log.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("Synthetic source map exceeds its bound")
    sources = {
        int(stamp): int(sequence)
        for stamp, sequence in re.findall(
            r"^quality-frame (\d+) (\d+)$", source_log.read_text(), re.M
        )
    }
    if not sources:
        raise ValueError("No synthetic stamp/sequence map was recorded")
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(encoded)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    stream = json.loads(probe.stdout)["streams"][0]
    width, height = stream["width"], stream["height"]
    source_width, source_height = config["width"], config["height"]
    if (width, height) != (source_width, source_height) and (
        config["encoder"] != "av1-vaapi"
        or width != (source_width + 63) // 64 * 64
        or height != (source_height + 15) // 16 * 16
    ):
        raise ValueError(
            "Quality comparison requires unchanged source pixels, with optional AV1 padding"
        )
    if not 80 <= width <= 4096 or not 64 <= height <= 2160:
        raise ValueError("Synthetic decoded dimensions exceed comparison bounds")
    block = 16 if config["content_pattern"] == "scroll16" else 4
    points = [
        (
            hash32(i * 2) % source_width,
            source_height // 5 + hash32(i * 2 + 1) % (source_height * 4 // 5),
        )
        for i in range(2048)
    ]
    errors, matched, skipped, frames = [], [], 0, 0
    with (directory / "quality-decode.log").open("w") as log:
        process = subprocess.Popen(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(encoded),
                "-frames:v",
                "240",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "pipe:1",
            ],
            stdout=subprocess.PIPE,
            stderr=log,
        )
        try:
            while frames < 240:
                data = process.stdout.read(width * height * 3)
                if not data:
                    break
                if len(data) != width * height * 3:
                    raise ValueError("Incomplete decoded frame or changed dimensions")
                frames += 1
                stamp = marker(data, width, height)
                sequence = sources.get(stamp)
                if sequence is None:
                    skipped += 1
                    continue
                error = 0
                for x, y in points:
                    value = hash32(
                        ((x + sequence * 7) // block) ^ hash32((y + sequence * 3) // block)
                    )
                    offset = (y * width + x) * 3
                    for channel in range(3):
                        error += (data[offset + channel] - ((value >> (channel * 8)) & 255)) ** 2
                mse = error / (len(points) * 3)
                errors.append(mse)
                matched.append({"sourceSequence": sequence, "sampledRGBMSE": mse})
            process.stdout.close()
            if process.wait(timeout=30):
                raise ValueError("Synthetic decode failed; inspect quality-decode.log")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
    if len(errors) < 30 or skipped:
        raise ValueError("Insufficient complete, matched synthetic frames")
    mse = sum(errors) / len(errors)
    result = {
        "matchedFrames": len(errors),
        "samplePointsPerFrame": len(points),
        "sampledRGBMSE": mse,
        "sampledRGBPSNR": 10 * math.log10(255**2 / mse) if mse else None,
        "decodedDimensions": [width, height],
        "exactSourceFraming": (width, height) == (source_width, source_height),
        "frames": matched,
        "scope": "Private exact-source pixel samples, includes color conversion; not VMAF, Windows parity or live-call quality",
    }
    (directory / "quality-comparison.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    print(json.dumps(analyze(parser.parse_args().directory)))
