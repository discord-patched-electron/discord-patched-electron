"""Build DPE's KMS producer and bundled privileged server without opening a GPU."""

import argparse
import json
import shutil
from pathlib import Path

from build_rust_support import build as build_rust
from common import ROOT, read_json, sha256, write_json


def build(output):
    output.mkdir(parents=True, exist_ok=True)
    executable, rust = build_rust("dpe-source", binary="dpe-kms-source")
    target = output / "dpe-kms-source"
    shutil.copy2(executable, target)
    server_binary, server_rust = build_rust("dpe-kms", binary="dpe-kms-server")
    pinned = ROOT / "integration/video/vendor/gsr-kms-server.inc"
    provenance = read_json(pinned.with_suffix(".json"))
    if sha256(pinned) != provenance["source_sha256"]:
        raise ValueError("Pinned GSR DRM exporter source changed")
    server = output / "dpe-kms-server"
    shutil.copy2(server_binary, server)
    write_json(
        output / "kms-server.json",
        {
            "sha256": sha256(server),
            "protocol": 6,
            "permissionVersion": 4,
            "executable": server.name,
            "license": "GPL-3.0-only",
            "source": provenance,
            "implementation": "rust",
            "rust": server_rust,
        },
    )
    report = {
        "sha256": sha256(target),
        "backend": "kms",
        "protocol": 6,
        "executable": target.name,
        "license": "GPL-3.0-only",
        "installed": False,
        "hardwareValidated": False,
        "rust": rust,
        "implementation": "rust",
    }
    write_json(output / "kms-source.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.output)))
