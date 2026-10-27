"""Verify package payloads, executable modes and dependency metadata."""

import argparse
import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
from pathlib import Path

from application_payload import validate_bootstrap
from common import read_json, sha256, write_json
from stage_updater import validate_distribution, validate_payload_entry


def check_client_free_archive(bundle, root):
    policy = json.load(bundle.extractfile(root + "/client-distribution.json"))
    if policy.get("mode") != "module-only" or policy.get("bundled_discord_client") is not False:
        raise ValueError("Package is not a module-only distribution")
    loader = bundle.extractfile(root + "/resources/app.asar").read()
    if hashlib.sha256(loader).hexdigest() != policy.get("loader_sha256"):
        raise ValueError("Package contains an unexpected bootstrap ASAR")
    for entry in bundle.getmembers():
        if entry.name.startswith(root + "/") or not (entry.issym() or entry.islnk()):
            validate_payload_entry(
                entry.name.removeprefix(root + "/"),
                is_dir=entry.isdir(),
                link=entry.linkname if entry.issym() or entry.islnk() else None,
                allow_runtime=False,
            )


def check_bootstrap_archive(bundle, root):
    policy = json.load(bundle.extractfile(root + "/bootstrap.json"))
    if policy.get("schema") != 1 or policy["application"].get("asset") != "application.zip":
        raise ValueError("Package is not a minimal bootstrap distribution")
    allowed = {
        "loader.py",
        "launch.sh",
        "bootstrap.json",
        "release-signing-key.asc",
        "discord.png",
        "README.txt",
        "SHA256SUMS",
        "licenses",
    }
    for entry in bundle.getmembers():
        if entry.name.startswith(root + "/"):
            relative = Path(entry.name.removeprefix(root + "/"))
            if (
                entry.issym()
                or entry.islnk()
                or (
                    relative.parts[0] not in allowed
                    and not (len(relative.parts) == 1 and relative.suffix == ".desktop")
                )
            ):
                raise ValueError("Unexpected application code in installer: " + entry.name)


def check(directory: Path):
    directory = directory.resolve()
    manifest = read_json(directory / "release.json")
    for filename, expected in manifest["assets"].items():
        if sha256(directory / filename) != expected:
            raise ValueError(f"Release checksum mismatch: {filename}")
    archive = next(directory.glob("*.tar.gz"))
    with tarfile.open(archive) as bundle:
        paths = bundle.getnames()
        root = paths[0]
        check_bootstrap_archive(bundle, root)
        if (
            json.load(bundle.extractfile(root + "/bootstrap.json"))["application"]
            != manifest["application"]
        ):
            raise ValueError("Bootstrap pins differ from the application release")
        checksums = bundle.extractfile(root + "/SHA256SUMS").read().decode()
        expected = dict(line.split("  ", 1)[::-1] for line in checksums.splitlines())
        for path, checksum in expected.items():
            if hashlib.sha256(bundle.extractfile(root + "/" + path).read()).hexdigest() != checksum:
                raise ValueError(f"Tarball payload mismatch: {path}")
        if bundle.getmember(root + "/launch.sh").mode & 0o111 != 0o111:
            raise ValueError("Tarball launcher is not executable")
        if any(member.isfile() and member.mode & 0o444 != 0o444 for member in bundle.getmembers()):
            raise ValueError("Portable files must be readable after root-owned installation")
        if any(member.mode & 0o6000 for member in bundle.getmembers()):
            raise ValueError("Portable tarball has privileged file modes")
    with tempfile.TemporaryDirectory(prefix="application-check-") as temporary:
        from importlib.util import module_from_spec, spec_from_file_location

        from common import ROOT

        spec = spec_from_file_location("dpe_bootstrap", ROOT / "integration/bootstrap/loader.py")
        loader = module_from_spec(spec)
        spec.loader.exec_module(loader)
        generation = Path(temporary)
        loader.extract(directory / "application.zip", generation / "app")
        loader.validate(generation, manifest["application"])
        validate_distribution(generation / "app", allow_runtime=False)
        if read_json(generation / "app/client-update.json") != manifest["client"]:
            raise ValueError("Application module pins differ from the signed release")
    deb = next(directory.glob("*.deb"))
    rpm = next(directory.glob("*.rpm"))
    # dpkg-deb is available in release CI; bsdtar supplies the local Arch path.
    data = subprocess.check_output(["bsdtar", "-xOf", str(deb), "data.tar.gz"])
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as package:
        package_root = next(
            name.removesuffix("/bootstrap.json")
            for name in package.getnames()
            if name.endswith("/bootstrap.json")
        )
        check_bootstrap_archive(package, package_root)
        for path, checksum in expected.items():
            member = next(
                (name for name in package.getnames() if name.lstrip("./") == f"opt/{root}/{path}"),
                None,
            )
            if (
                member is not None
                and package.getmember(member).isfile()
                and package.getmember(member).mode & 0o444 != 0o444
            ):
                raise ValueError(f"Debian payload is not readable: {path}")
            if (
                member is None
                or hashlib.sha256(package.extractfile(member).read()).hexdigest() != checksum
            ):
                raise ValueError(f"Debian payload mismatch: {path}")
    arch = next(directory.glob("*.pkg.tar.zst"))
    for packaged in (rpm, arch):
        with tempfile.TemporaryDirectory(prefix="release-package-") as temporary:
            subprocess.run(["bsdtar", "-xf", str(packaged), "-C", temporary], check=True)
            validate_bootstrap(Path(temporary) / "opt" / root)
            for path, checksum in expected.items():
                installed = Path(temporary) / "opt" / root / path
                if installed.stat().st_mode & 0o444 != 0o444:
                    raise ValueError(f"{packaged.name} payload is not readable: {path}")
                if sha256(installed) != checksum:
                    raise ValueError(f"{packaged.name} payload mismatch: {path}")
    arch_info = subprocess.check_output(["bsdtar", "-xOf", str(arch), ".PKGINFO"], text=True)
    for field in (
        f"pkgname = {root}",
        "arch = x86_64",
        "depend = python>=3.11",
        "depend = pipewire",
    ):
        if field not in arch_info.splitlines():
            raise ValueError(f"Arch package lacks metadata: {field}")
    requires = subprocess.check_output(["rpm", "-qpR", str(rpm)], text=True)
    if "python3" not in requires or "pipewire-utils" not in requires:
        raise ValueError("RPM lacks helper dependencies")
    report = {
        "passed": True,
        "channel": manifest["channel"],
        "assets": manifest["assets"],
        "same_app_payload_in_all_formats": True,
        "bundled_discord_client": False,
        "bundled_electron": False,
        "payload_files": len(expected),
        "rpm_dependency_install_verified": False,
    }
    write_json(directory / "package-checks.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    check(args.directory)
