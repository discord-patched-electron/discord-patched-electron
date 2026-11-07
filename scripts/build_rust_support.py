"""Build the pinned, memory-safe native support archive independently of Electron."""

import fcntl
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from common import ROOT, digest, hash_files, sha256


def inputs():
    return [
        "Cargo.toml",
        "Cargo.lock",
        "rust-toolchain.toml",
        ".cargo/config.toml",
        "scripts/build_rust_support.py",
        "integration/video/encoder-abi.h",
        "integration/video/capture-abi.h",
        "integration/video/capture-delivery.h",
        "integration/video/capture-picker.h",
        "integration/video/source-pods.h",
        "integration/video/native-pointer.h",
        "integration/video/direct-capture.h",
        "integration/video/direct-capture-gpu.h",
        "integration/video/direct-capture-wire.h",
        "integration/video/vkcapture-relay.h",
        "integration/video/vkcapture-wire.h",
        "integration/video/vkcapture-gl-sync.h",
        "integration/video/kms-client.h",
        "integration/video/kms-gpu.h",
        "integration/video/kms-protocol.h",
        "integration/video/protocols/GSR-GPL-3.0.txt",
        *sorted(
            str(path.relative_to(ROOT)) for path in (ROOT / "native").rglob("*") if path.is_file()
        ),
    ]


def build(package="dpe-native", *, capture_gpu=True, features=(), environment=None, binary=None):
    target = ROOT / "out/rust-target"
    target.mkdir(parents=True, exist_ok=True)
    # Cargo releases its own lock before consumers link the archive. Different
    # feature builds otherwise overwrite that path between return and linking.
    with (target / "dpe-build.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _build(package, capture_gpu, features, environment, binary)


def _build(package, capture_gpu, features, environment, binary):
    subprocess.run(
        [
            "cargo",
            "build",
            "--locked",
            "--release",
            "-p",
            package,
            *(["--bin", binary] if binary else []),
            *(["--features", ",".join(features)] if features else []),
            *([] if capture_gpu else ["--no-default-features"]),
        ],
        cwd=ROOT,
        check=True,
        env={**os.environ, **(environment or {})},
    )
    archive = (
        ROOT
        / "out/rust-target/release"
        / (binary if binary else f"lib{package.replace('-', '_')}.a")
    )
    checksum = sha256(archive)
    retained = ROOT / "out/rust-support" / checksum / archive.name
    retained.parent.mkdir(parents=True, exist_ok=True)
    if not retained.is_file() or sha256(retained) != checksum:
        with tempfile.NamedTemporaryFile(dir=retained.parent, delete=False) as output:
            temporary = Path(output.name)
        try:
            shutil.copy2(archive, temporary)
            if sha256(temporary) != checksum:
                raise ValueError("Rust archive changed before it could be retained")
            temporary.replace(retained)
        finally:
            temporary.unlink(missing_ok=True)
    return retained, {
        "source_key": digest(hash_files(inputs())),
        "package": package,
        **({"binary": binary} if binary else {}),
        "capture_gpu": capture_gpu,
        "features": list(features),
        "sha256": checksum,
        "compiler": subprocess.check_output(["rustc", "--version"], cwd=ROOT, text=True).strip(),
    }


def stage_source(output):
    """Retain rebuild inputs/notices without requiring a compiler to repackage."""
    output = Path(output)
    for name in [*inputs(), "scripts/common.py", "LICENSE"]:
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
