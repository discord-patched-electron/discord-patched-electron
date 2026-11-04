"""Build the optional direct Wayland GPU/PipeWire producer; no capture is started."""

import argparse
import json
import subprocess
from pathlib import Path

from build_wayland_capture import protocols
from common import ROOT, sha256, write_json


def build(output, compiler="cc"):
    generated = protocols(output / "protocols")
    flags = subprocess.check_output(
        ["pkg-config", "--cflags", "--libs", "wayland-client", "libpipewire-0.3", "gbm", "libdrm"],
        text=True,
    ).split()
    target = output / "dpe-wayland-source"
    subprocess.run(
        [
            compiler,
            "-std=c11",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-Wl,-z,relro,-z,now",
            "-I" + str(output / "protocols"),
            str(ROOT / "integration/video/wayland-source.c"),
            str(ROOT / "integration/video/wayland-capture.c"),
            *map(str, generated),
            *flags,
            "-o",
            str(target),
        ],
        check=True,
    )
    report = dict(
        sha256=sha256(target),
        scope="direct output GPU producer; build does not open a compositor or GPU",
        backend="wayland-direct",
        executable=target.name,
        installed=False,
    )
    write_json(output / "wayland-source.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.output)))
