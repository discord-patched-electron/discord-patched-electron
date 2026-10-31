"""Sign the Arch package and checksum list with the dedicated project key."""

import argparse
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from common import ROOT, read_json, sha256

PUBLIC_KEY = ROOT / "packaging/release-signing-key.asc"
KEY_ASSET = "release-signing-key.asc"


def payloads(directory: Path) -> list[Path]:
    assets = read_json(directory / "release.json")["assets"]
    archives = [name for name in assets if name.endswith(".pkg.tar.zst")]
    if len(archives) != 1 or Path(archives[0]).name != archives[0]:
        raise ValueError("Expected one Arch package with a plain filename")
    return [directory / archives[0], directory / "SHA256SUMS"]


def signature_files(directory: Path) -> list[Path]:
    return [Path(str(path) + ".sig") for path in payloads(directory)] + [directory / KEY_ASSET]


def verify_checksums(directory: Path):
    """Bind the signed list to the manifest and every payload it describes."""
    sums = {}
    for line in (directory / "SHA256SUMS").read_text().splitlines():
        match = re.fullmatch(r"([a-f0-9]{64})  ([A-Za-z0-9._+-]+)", line)
        if not match or match[2] in sums:
            raise ValueError("Invalid or duplicate signed checksum entry")
        sums[match[2]] = match[1]
    if sums.get("release.json") != sha256(directory / "release.json"):
        raise ValueError("Release manifest differs from its signed checksum")
    assets = read_json(directory / "release.json")["assets"]
    if not isinstance(assets, dict) or set(sums) != {*assets, "release.json"}:
        raise ValueError("Signed checksum list must cover exactly the release assets and manifest")
    for name, expected in assets.items():
        if (
            name in ("release.json", "SHA256SUMS")
            or sums.get(name) != expected
            or sha256(directory / name) != expected
        ):
            raise ValueError("Release asset differs from its signed checksum: " + name)


def verify(directory: Path, *, required: bool = True):
    files = signature_files(directory)
    if not required and not any(path.exists() for path in files):
        return
    if not all(path.is_file() for path in files):
        raise ValueError("Release signatures or public key are missing")
    if (directory / KEY_ASSET).read_bytes() != PUBLIC_KEY.read_bytes():
        raise ValueError("Release signing key differs from the pinned project key")
    _verify_signatures([(Path(str(path) + ".sig"), path) for path in payloads(directory)])
    verify_checksums(directory)


def _verify_signatures(pairs: list[tuple[Path, Path]]):
    """Verify supplied signatures; callers separately enforce completeness."""
    if not pairs:
        return
    with tempfile.TemporaryDirectory(prefix="release-verify-") as temporary:
        keyring = Path(temporary) / "public.gpg"
        subprocess.run(
            [
                "gpg",
                "--batch",
                "--homedir",
                temporary,
                "--dearmor",
                "--output",
                str(keyring),
                str(PUBLIC_KEY),
            ],
            check=True,
            capture_output=True,
        )
        for signature, path in pairs:
            subprocess.run(
                [
                    "gpgv",
                    "--homedir",
                    temporary,
                    "--keyring",
                    str(keyring),
                    str(signature),
                    str(path),
                ],
                check=True,
                capture_output=True,
            )


def sign(directory: Path, secret: bytes):
    if not secret:
        raise ValueError("Configure the RELEASE_SIGNING_KEY Actions secret")
    verify_checksums(directory)
    key = directory / KEY_ASSET
    if key.exists() and key.read_bytes() != PUBLIC_KEY.read_bytes():
        raise ValueError("Release signing key differs from the pinned project key")
    pairs = [(Path(str(path) + ".sig"), path) for path in payloads(directory)]
    _verify_signatures([(signature, path) for signature, path in pairs if signature.exists()])
    missing = [(signature, path) for signature, path in pairs if not signature.exists()]
    if not missing and key.is_file():
        return
    # Prepare and verify before exposing new artifacts. Publication can itself be
    # interrupted; valid existing files are checked above and retained on retry.
    with (
        tempfile.TemporaryDirectory(prefix=".release-sign-", dir=directory) as staging,
        tempfile.TemporaryDirectory(prefix="release-sign-") as temporary,
    ):
        staged = [(Path(staging) / signature.name, path) for signature, path in missing]
        command = ["gpg", "--batch", "--homedir", temporary]
        try:
            if staged:
                subprocess.run(
                    [*command, "--import"], input=secret, check=True, capture_output=True
                )
            for signature, path in staged:
                subprocess.run(
                    [
                        *command,
                        "--pinentry-mode",
                        "loopback",
                        "--passphrase",
                        "",
                        "--detach-sign",
                        "--output",
                        str(signature),
                        str(path),
                    ],
                    check=True,
                    capture_output=True,
                )
        finally:
            subprocess.run(
                ["gpgconf", "--homedir", temporary, "--kill", "gpg-agent"],
                check=False,
                capture_output=True,
            )
        _verify_signatures(staged)
        shutil.copyfile(PUBLIC_KEY, Path(staging) / KEY_ASSET)
        for path in [*(signature for signature, _ in staged), Path(staging) / KEY_ASSET]:
            try:
                # Same-filesystem exclusive publication never overwrites a file
                # from an earlier or concurrent attempt. Verify the result below.
                os.link(path, directory / path.name)
            except FileExistsError:
                pass
    verify(directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["sign", "verify"])
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    # Import here so release publication can use signature verification too.
    from releases import verify_assets

    verify_assets(args.directory, read_json(args.directory / "release.json"))
    if args.action == "sign":
        sign(args.directory, os.environ.pop("RELEASE_SIGNING_KEY", "").encode())
    else:
        verify(args.directory)
    print("Release signatures verified.")


if __name__ == "__main__":
    main()
