"""Sign the Arch package and checksum list with the dedicated project key."""

import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from common import ROOT, read_json

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


def verify(directory: Path, *, required: bool = True):
    files = signature_files(directory)
    if not required and not any(path.exists() for path in files):
        return
    if not all(path.is_file() for path in files):
        raise ValueError("Release signatures or public key are missing")
    if (directory / KEY_ASSET).read_bytes() != PUBLIC_KEY.read_bytes():
        raise ValueError("Release signing key differs from the pinned project key")
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
        for path in payloads(directory):
            subprocess.run(
                [
                    "gpgv",
                    "--homedir",
                    temporary,
                    "--keyring",
                    str(keyring),
                    str(path) + ".sig",
                    str(path),
                ],
                check=True,
                capture_output=True,
            )


def sign(directory: Path, secret: bytes):
    if not secret:
        raise ValueError("Configure the RELEASE_SIGNING_KEY Actions secret")
    if any(path.exists() for path in signature_files(directory)):
        verify(directory)
        return
    with tempfile.TemporaryDirectory(prefix="release-sign-") as temporary:
        command = ["gpg", "--batch", "--homedir", temporary]
        try:
            subprocess.run([*command, "--import"], input=secret, check=True, capture_output=True)
            for path in payloads(directory):
                subprocess.run(
                    [
                        *command,
                        "--pinentry-mode",
                        "loopback",
                        "--passphrase",
                        "",
                        "--detach-sign",
                        "--output",
                        str(path) + ".sig",
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
    shutil.copyfile(PUBLIC_KEY, directory / KEY_ASSET)
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
