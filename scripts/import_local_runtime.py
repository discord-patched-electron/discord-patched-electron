"""Verify a repository-pinned local build for normal CI qualification."""

import argparse
import re
from pathlib import Path

from common import ROOT, download, read_json, sha256
from fetch_reference import CACHE_REPOSITORY, extract, validate_capacity


def import_runtime(output: Path, pin: dict):
    key = pin.get("runtime_key", "")
    assets = pin.get("assets", {})
    if (
        pin.get("schema") != 1
        or not re.fullmatch(r"[a-f0-9]{64}", key)
        or set(assets) != {"runtime-dist.zip", "capacity.json", "local-build-provenance.json"}
        or any(not re.fullmatch(r"[a-f0-9]{64}", str(value)) for value in assets.values())
    ):
        raise ValueError("Invalid pinned local runtime")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use an empty import directory")
    capacity_dir = output / "capacity"
    capacity_dir.mkdir(parents=True, exist_ok=True)
    base = f"https://github.com/{CACHE_REPOSITORY}/releases/download/runtime-cache-{key}"

    def asset(name, directory):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        download(base + "/" + name, path, assets[name])
        if sha256(path) != assets[name]:
            raise ValueError("Local runtime asset checksum mismatch: " + name)
        return path

    capacity = read_json(asset("capacity.json", capacity_dir))
    validate_capacity(capacity, True)
    provenance = read_json(asset("local-build-provenance.json", capacity_dir))
    if (
        capacity.get("runtime_key") != key
        or capacity.get("build_key") != key
        or capacity.get("archive_sha256") != assets["runtime-dist.zip"]
        or provenance.get("compiler_provider") != "local"
        or provenance.get("runtime_key") != key
        or provenance.get("archive_sha256") != assets["runtime-dist.zip"]
        or provenance.get("archive_bytes") != capacity.get("archive_bytes")
        or provenance.get("source_tree") != capacity["source"].get("tree")
        or provenance.get("publishable") is not False
    ):
        raise ValueError("Local runtime provenance disagrees with pinned compiler report")
    archive = asset("runtime-dist.zip", output / "binary")
    extract(archive, output / "runtime", assets["runtime-dist.zip"])
    return capacity


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    import_runtime(args.output, read_json(ROOT / "config/local-runtime.json"))
