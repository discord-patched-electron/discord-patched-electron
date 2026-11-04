"""Build or verify the shipped optional capture backends, without opening a GPU."""

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from build_kms_source import build as build_kms
from build_screen_preview import build as build_preview
from build_vkcapture_backend import build as build_game
from build_wayland_source import build as build_wayland
from common import ROOT, download, read_json, sha256, write_json
from stage_vkcapture import ARCHIVE_SHA256, COMMIT, URL

REQUIRED = {
    "dpe-wayland-source",
    "dpe-kms-source",
    "dpe-kms-server",
    "kms-server.json",
    "dpe-screen-preview",
    "screen-preview.json",
    "kms-source.json",
    "dpe-source/kms-source.c",
    "dpe-source/kms-protocol.h",
    "dpe-source/kms-README.md",
    "dpe-source/kms-permission-setup.py",
    "dpe-source/kms-server.c",
    "dpe-source/vendor/gsr-kms-server.inc",
    "dpe-source/vendor/gsr-kms-server.json",
    "dpe-source/protocols/GSR-GPL-3.0.txt",
    "vkcapture/dpe-vkcapture-source",
    "vkcapture/dpe-vkcapture-preview",
    "vkcapture/vkcapture-broker.py",
    "vkcapture/gamecapture-launch.py",
    "vkcapture/libobs_glcapture.so",
    "vkcapture/libVkLayer_obs_vkcapture.so",
    "vkcapture/layers/dpe_vkcapture.json",
    "vkcapture/backend.json",
}


def validate(directory):
    directory = Path(directory)
    report = read_json(directory / "capture-bundle.json")
    files = report.get("files", {})
    if (
        report.get("schema") != 1
        or report.get("upstreamCommit") != COMMIT
        or not REQUIRED <= files.keys()
    ):
        raise ValueError("Incomplete capture bundle")
    actual = set()
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise ValueError("Capture bundles cannot contain symbolic links")
        if path.is_file() and path.relative_to(directory).as_posix() != "capture-bundle.json":
            actual.add(path.relative_to(directory).as_posix())
    if actual != set(files):
        raise ValueError("Capture bundle inventory differs")
    for name, checksum in files.items():
        if (
            Path(name).is_absolute()
            or ".." in Path(name).parts
            or sha256(directory / name) != checksum
        ):
            raise ValueError("Capture bundle checksum mismatch")
    hook_source = directory / f"vkcapture/source/obs-vkcapture-{COMMIT}"
    if (
        not (hook_source / "LICENSE").is_file()
        or not (hook_source / "src/vkcapture-hook.c").is_file()
    ):
        raise ValueError("Capture hook corresponding source is missing")
    return report


def stage(output, *, archive=None, prebuilt=None):
    output = Path(output)
    if output.exists():
        raise ValueError("Choose a fresh capture bundle directory")
    supplied = Path(prebuilt) if prebuilt is not None else ROOT / "prebuilt-capture"
    if supplied.exists():
        report = validate(supplied)
        shutil.copytree(supplied, output)
        return report
    if prebuilt is not None:
        raise ValueError("Prebuilt capture bundle is missing")
    if archive is None:
        cache = ROOT / "out/capture-source-cache/obs-vkcapture.tar.gz"
        archive = download(URL, cache, ARCHIVE_SHA256)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="capture-build-", dir=output.parent) as temporary:
        work = Path(temporary)
        build_wayland(work / "wayland")
        build_kms(work / "kms")
        build_preview(work / "preview")
        build_game(work / "game", Path(archive))
        output.mkdir()
        for name in ("dpe-wayland-source", "wayland-source.json"):
            shutil.copy2(work / "wayland" / name, output / name)
        for name in ("dpe-kms-source", "kms-source.json", "dpe-kms-server", "kms-server.json"):
            shutil.copy2(work / "kms" / name, output / name)
        for name in ("dpe-screen-preview", "screen-preview.json"):
            shutil.copy2(work / "preview" / name, output / name)
        shutil.copytree(work / "game", output / "vkcapture", ignore=shutil.ignore_patterns("build"))
        source = output / "dpe-source"
        shutil.copytree(
            ROOT / "integration/video",
            source,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        shutil.copy2(ROOT / "LICENSE", source / "LICENSE")
    report = {
        "schema": 1,
        "architecture": "x86_64",
        "upstreamCommit": COMMIT,
        "upstreamArchiveSha256": ARCHIVE_SHA256,
        "hardwareValidated": False,
        "files": {
            p.relative_to(output).as_posix(): sha256(p)
            for p in sorted(output.rglob("*"))
            if p.is_file()
        },
    }
    write_json(output / "capture-bundle.json", report)
    for path in output.rglob("*"):
        if path.is_file():
            path.chmod(0o755 if path.stat().st_mode & 0o111 else 0o644)
    validate(output)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--prebuilt", type=Path)
    args = parser.parse_args()
    result = stage(args.output, archive=args.archive, prebuilt=args.prebuilt)
    print(
        json.dumps(
            {"schema": result["schema"], "files": len(result["files"]), "output": str(args.output)}
        )
    )
