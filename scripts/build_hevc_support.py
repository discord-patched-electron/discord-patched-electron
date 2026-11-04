"""Build the isolated HEVC VA-API backend, independently of Electron.

The source archive is fixed and verified. Retain the cache (including FFmpeg
objects) between builds. No compiler is started at application runtime.
"""

import argparse
import fcntl
import os
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path

from common import ROOT, digest, download, hash_files, read_json, sha256, write_json

VERSION = "9.0.2"
ARCHIVE_SHA256 = "8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e"
URL = f"https://ffmpeg.org/releases/ffmpeg-{VERSION}.tar.xz"
CONFIGURE = [
    "--disable-everything",
    "--disable-autodetect",
    "--disable-programs",
    "--disable-doc",
    "--disable-debug",
    "--disable-network",
    "--disable-shared",
    "--enable-static",
    "--enable-pic",
    "--enable-vaapi",
    "--enable-libdrm",
    "--enable-encoder=hevc_vaapi,av1_vaapi",
    "--disable-avdevice",
    "--disable-avformat",
    "--disable-avfilter",
    "--disable-swscale",
    "--disable-swresample",
    "--extra-cflags=-fvisibility=hidden",
]
SOURCE = "integration/video/hevc-backend.c"
HEADER = "integration/video/encoder-abi.h"
ASSET = "libdpe-hevc.so"
SOURCE_ARCHIVE = f"ffmpeg-{VERSION}.tar.xz"


def stage_source(output, archive=None):
    """Ship complete upstream source beside the shipped adapter/build patch."""
    destination = output / "sources" / SOURCE_ARCHIVE
    destination.parent.mkdir(parents=True, exist_ok=True)
    if archive is not None:
        if sha256(archive) != ARCHIVE_SHA256:
            raise ValueError("FFmpeg source archive checksum mismatch")
        shutil.copy2(archive, destination)
    else:
        cache = Path(os.environ.get("DPE_HEVC_BUILD_CACHE", ROOT / "out/hevc-build"))
        retained = cache / SOURCE_ARCHIVE
        if retained.is_file():
            return stage_source(output, retained)
        download(URL, destination, ARCHIVE_SHA256)
    return destination


def prepare_source(cache):
    archive = cache / f"ffmpeg-{VERSION}.tar.xz"
    if not archive.exists():
        temporary = archive.with_suffix(".download")
        try:
            with urllib.request.urlopen(URL, timeout=60) as response, temporary.open("wb") as out:
                shutil.copyfileobj(response, out)
            if sha256(temporary) != ARCHIVE_SHA256:
                raise ValueError("FFmpeg source archive checksum mismatch")
            temporary.replace(archive)
        finally:
            temporary.unlink(missing_ok=True)
    if sha256(archive) != ARCHIVE_SHA256:
        raise ValueError("FFmpeg source archive checksum mismatch")
    source = cache / f"ffmpeg-{VERSION}"
    if not source.exists():
        with tarfile.open(archive) as contents:
            contents.extractall(cache, filter="data")
    # Private codec state is compiled with these exact headers. FFmpeg's normal
    # IDR-only globals do not apply dynamic Discord bitrate/FPS updates.
    file = source / "libavcodec/vaapi_encode.c"
    text = file.read_text()
    before = """    if (base_pic->type == FF_HW_PICTURE_TYPE_IDR) {
        for (i = 0; i < ctx->nb_global_params; i++) {"""
    unconditional = """    // DPE: submit current RC/HRD/FPS globals between IDRs too.
    {
        for (i = 0; i < ctx->nb_global_params; i++) {"""
    after = """    // DPE: submit current RC/HRD/FPS only when changed between IDRs.
    if (base_pic->type == FF_HW_PICTURE_TYPE_IDR || ctx->dpe_rate_update_pending) {
        ctx->dpe_rate_update_pending = 0;
        for (i = 0; i < ctx->nb_global_params; i++) {
            if (base_pic->type != FF_HW_PICTURE_TYPE_IDR &&
                ctx->global_params_type[i] != VAEncMiscParameterTypeRateControl &&
                ctx->global_params_type[i] != VAEncMiscParameterTypeHRD &&
                ctx->global_params_type[i] != VAEncMiscParameterTypeFrameRate)
                continue;"""
    historical = """    // Private Discord experiment: rate-control globals can change between IDRs.
    {
        for (i = 0; i < ctx->nb_global_params; i++) {"""
    if text.count(before) == 1:
        file.write_text(text.replace(before, after))
    elif text.count(historical) == 1:
        file.write_text(text.replace(historical, after))
    elif text.count(unconditional) == 1:
        file.write_text(text.replace(unconditional, after))
    elif text.count(after) != 1:
        raise ValueError("FFmpeg VA-API rate-update patch no longer matches")
    file = source / "libavcodec/vaapi_encode.h"
    text = file.read_text()
    pending = "    int             dpe_rate_update_pending;\n"
    marker = "    VAEncMiscParameterRateControl rc_params;"
    if text.count(marker) != 1:
        raise ValueError("FFmpeg VA-API rate-update state no longer matches")
    if pending not in text:
        file.write_text(text.replace(marker, pending + marker))
    return source


def build(output, compiler="cc", cache=None):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    cache = Path(cache or os.environ.get("DPE_HEVC_BUILD_CACHE", ROOT / "out/hevc-build")).resolve()
    cache.mkdir(parents=True, exist_ok=True)
    identity = digest(
        {
            "archive": ARCHIVE_SHA256,
            "configure": CONFIGURE,
            "compiler": subprocess.check_output([compiler, "--version"], text=True),
            "source": hash_files(
                [
                    SOURCE,
                    HEADER,
                    "integration/video/av1-level.h",
                    "integration/video/hevc-level.h",
                    "scripts/build_hevc_support.py",
                ]
            ),
        }
    )
    with (cache / "build.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        manifest = cache / "hevc-support.json"
        asset = cache / ASSET
        if manifest.exists() and asset.exists():
            previous = read_json(manifest)
            if previous.get("source_key") == identity and previous.get("sha256") == sha256(asset):
                shutil.copy2(asset, output / ASSET)
                shutil.copy2(manifest, output / manifest.name)
                return previous
        source = prepare_source(cache)
        configuration = source / "dpe-configuration.json"
        desired = {"configure": CONFIGURE, "compiler": compiler}
        with (cache / "build.log").open("a") as log:
            if not configuration.exists() or read_json(configuration) != desired:
                subprocess.run(
                    ["./configure", *CONFIGURE, f"--cc={compiler}"],
                    cwd=source,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=True,
                )
                write_json(configuration, desired)
            subprocess.run(
                [
                    "make",
                    f"-j{min(6, os.cpu_count() or 1)}",
                    "libavcodec/libavcodec.a",
                    "libavutil/libavutil.a",
                ],
                cwd=source,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
            )
            subprocess.run(
                [
                    compiler,
                    "-std=c11",
                    "-O2",
                    "-fPIC",
                    "-fvisibility=hidden",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-shared",
                    "-Wl,-z,relro,-z,now",
                    "-Wl,--exclude-libs,ALL",
                    "-Wl,-Bsymbolic",
                    "-I" + str(source),
                    str(ROOT / SOURCE),
                    str(source / "libavcodec/libavcodec.a"),
                    str(source / "libavutil/libavutil.a"),
                    "-o",
                    str(asset),
                    "-lva",
                    "-lva-drm",
                    "-ldrm",
                    "-lm",
                    "-pthread",
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
            )
        report = {
            "schema": 1,
            "source_key": identity,
            "sha256": sha256(asset),
            "ffmpeg": {"version": VERSION, "source_url": URL, "source_sha256": ARCHIVE_SHA256},
            "configure": CONFIGURE,
            "license": "LGPL-2.1-or-later",
        }
        write_json(manifest, report)
        shutil.copy2(asset, output / ASSET)
        shutil.copy2(manifest, output / manifest.name)
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--compiler", default="cc")
    args = parser.parse_args()
    build(args.output, args.compiler, args.cache)
