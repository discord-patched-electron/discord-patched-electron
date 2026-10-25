"""Publish an AUR recipe for the exact portable app verified by release CI."""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

from common import ROOT, electron_channel, package_name, read_json, run, sha256
from desktop_identity import desktop_id
from package_release import metadata
from release_signing import verify as verify_signatures
from releases import (
    URL,
    api,
    approval_path,
    approve,
    manifest_tag,
    validate_approval,
    verify_assets,
)


def require_stable(release: dict):
    if release.get("draft") is not False or release.get("prerelease") is not False:
        raise ValueError("AUR publication requires an approved stable release")


def require_approved(release: dict, manifest: dict, approved: dict | None, checksum: str):
    require_stable(release)
    if (
        not approved
        or approved.get("schema") != 1
        or approved.get("tag") != release["tag_name"]
        or approved.get("version") != manifest["version"]
        or approved.get("manifest_sha256") != checksum
        or not approved.get("approved_by")
    ):
        raise ValueError("AUR publication requires approval of these exact package files")


def manifest_name(manifest: dict) -> str:
    return package_name(manifest["channel"], electron_channel(manifest["electron_version"]))


def recipe(manifest: dict, tag: str, revision: int = 1) -> str:
    if tag != manifest_tag(manifest):
        raise ValueError("Release identity mismatch")
    config = metadata(
        Path("/unused"),
        manifest["version"],
        manifest["channel"],
        0,
        electron_channel(manifest["electron_version"]),
    )
    dependencies = config["overrides"]["archlinux"]
    dependencies["depends"][0] = "glibc>=" + manifest["minimum_glibc"]
    legacy_id = (
        "io.github.discord_patched_electron."
        + {"stable": "Discord", "ptb": "DiscordPTB", "canary": "DiscordCanary"}[manifest["channel"]]
    )
    identity = manifest.get("desktop_id", legacy_id)
    if identity not in (legacy_id, desktop_id(manifest["channel"])):
        raise ValueError("Unexpected desktop identity")
    icon = identity if "desktop_id" in manifest else manifest_name(manifest)
    conflicts = " ".join(shlex.quote(name) for name in dependencies["conflicts"])
    ownership = (
        f"provides=('{identity}')\nconflicts=({conflicts})\n" if "desktop_id" in manifest else ""
    )
    command_link = (
        f'  ln -s "/opt/$pkgname/launch.sh" "$pkgdir/usr/bin/{identity}"\n'
        if "desktop_id" in manifest
        else ""
    )
    asset = next(name for name in manifest["assets"] if name.endswith(".tar.gz"))
    if Path(asset).name != asset or not re.fullmatch(r"[\w.-]+", asset):
        raise ValueError("Invalid portable asset name")
    checksum = manifest["assets"][asset]
    if not re.fullmatch(r"[a-f0-9]{64}", checksum):
        raise ValueError("Invalid archive checksum")

    def array(items):
        return "(" + " ".join(shlex.quote(item) for item in items) + ")"

    licenses = (
        config["license"].split(" AND ")
        if manifest.get("client_distribution") == "module-only"
        else ["LicenseRef-Discord", "MIT", "BSD-3-Clause"]
    )
    return f"""# Maintainer: he3als <he3als@users.noreply.github.com>
pkgname={config["name"]}
pkgver={manifest["version"]}
pkgrel={revision}
pkgdesc='Discord {manifest["channel"].title()} with patched Electron and Linux fixes (AVX2 required)'
arch=('x86_64')
url='{URL}'
license={array(licenses)}
depends={array(dependencies["depends"])}
optdepends={array(dependencies["recommends"])}
options=('!strip' '!debug')
{ownership}\
install=discord.install
source=("$pkgname-$pkgver.tar.gz::{URL}/releases/download/{tag}/{asset}")
sha256sums=('{checksum}')

package() {{
  install -dm755 "$pkgdir/opt" "$pkgdir/usr/bin"
  cp -a "$srcdir/$pkgname" "$pkgdir/opt/$pkgname"
  ln -s "/opt/$pkgname/launch.sh" "$pkgdir/usr/bin/$pkgname"
{command_link}\
  install -Dm644 "$srcdir/$pkgname/{identity}.desktop" \\
    "$pkgdir/usr/share/applications/{identity}.desktop"
  install -Dm644 "$srcdir/$pkgname/discord.png" "$pkgdir/usr/share/icons/hicolor/256x256/apps/{icon}.png"
  install -dm755 "$pkgdir/usr/share/licenses/$pkgname"
  cp -a "$srcdir/$pkgname/licenses/." "$pkgdir/usr/share/licenses/$pkgname/"
}}
"""


def stage(directory: Path, manifest: dict, tag: str):
    name = manifest_name(manifest)
    directory.mkdir(parents=True, exist_ok=True)
    previous = (
        read_srcinfo((directory / ".SRCINFO").read_text())
        if (directory / ".SRCINFO").exists()
        else {}
    )
    revision = (
        int(previous.get("pkgrel", ["1"])[0])
        if previous.get("pkgver") == [manifest["version"]]
        else 1
    )
    (directory / "PKGBUILD").write_text(recipe(manifest, tag, revision))
    (directory / "discord.install").write_text(
        "post_install() {\n"
        f"  sh /opt/{name}/refresh-mod-backup.sh /opt/{name}\n"
        "}\n\npost_upgrade() {\n  post_install\n}\n"
    )
    write_srcinfo(directory)


def read_srcinfo(text: str) -> dict[str, list[str]]:
    fields = {}
    for line in text.splitlines():
        if " = " in line:
            key, value = line.strip().split(" = ", 1)
            fields.setdefault(key, []).append(value)
    return fields


def write_srcinfo(directory: Path):
    command = ["makepkg", "--printsrcinfo"]
    if not shutil.which("makepkg"):
        command = [
            "docker",
            "run",
            "--rm",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "-v",
            f"{directory.resolve()}:/package",
            "-w",
            "/package",
            "archlinux:base@sha256:52dfbd5afb23a220d5ee92dafcb4c788d96c026ed04179890c23adef4c897fa0",
            *command,
        ]
    result = subprocess.check_output(command, cwd=directory, text=True)
    (directory / ".SRCINFO").write_text(result)


def publish(directory: Path, manifest: dict):
    # Never overwrite a newer AUR package when jobs finish out of order.
    previous = subprocess.run(
        ["git", "show", "HEAD:.SRCINFO"], cwd=directory, capture_output=True, text=True
    )
    match = re.search(r"^\s*pkgver = ([0-9.]+)$", previous.stdout, re.MULTILINE)
    if match:
        installed = tuple(map(int, match[1].split(".")))
        requested = tuple(map(int, manifest["version"].split(".")))
        if installed > requested:
            raise ValueError("AUR already has a newer version")
    run("git", "add", "PKGBUILD", ".SRCINFO", "discord.install", cwd=directory)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=directory).returncode == 0:
        print("AUR package is already current.")
        return
    if match and installed == requested:
        old = read_srcinfo(previous.stdout)
        current = read_srcinfo((directory / ".SRCINFO").read_text())
        if any(old.get(key) != current.get(key) for key in ("source", "sha256sums")):
            raise ValueError(
                "Refusing to change archive contents for an already published AUR version"
            )
        revision = int(old["pkgrel"][0]) + 1
        path = directory / "PKGBUILD"
        path.write_text(
            re.sub(r"^pkgrel=\d+$", f"pkgrel={revision}", path.read_text(), flags=re.MULTILINE)
        )
        write_srcinfo(directory)
        run("git", "add", "PKGBUILD", ".SRCINFO", cwd=directory)
    run(
        "git",
        "-c",
        "user.name=he3als",
        "-c",
        "user.email=he3als@users.noreply.github.com",
        "commit",
        "-m",
        "Update to " + manifest["version"],
        cwd=directory,
    )
    run("git", "push", "origin", "HEAD:master", cwd=directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", nargs="?")
    parser.add_argument(
        "--manifest", type=Path, help="Generate a recipe locally, without publishing"
    )
    parser.add_argument("--verified-directory", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "out/aur-publish")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    if args.manifest:
        if args.publish or args.tag or args.verified_directory:
            parser.error("--manifest only generates a local recipe")
        manifest = read_json(args.manifest)
        verify_assets(args.manifest.parent, manifest)
        stage(
            args.output,
            manifest,
            manifest_tag(manifest),
        )
        return
    if not args.tag:
        parser.error("A public app release tag is required")
    release = api("releases/tags/" + args.tag)
    if args.publish:
        require_stable(release)
    directory = args.verified_directory or approve(args.tag, check_only=True)
    manifest = read_json(directory / "release.json")
    verify_assets(directory, manifest)
    verify_signatures(directory, required=manifest.get("signing_required", False))
    if args.publish:
        path = approval_path(manifest)
        require_approved(
            release,
            manifest,
            read_json(path) if path.exists() else None,
            sha256(directory / "release.json"),
        )
    validate_approval(release, manifest, api("actions/runs/" + manifest["workflow_run"]), None)
    if api("commits/" + args.tag)["sha"] != manifest["source_commit"]:
        raise ValueError("Release tag changed")
    uploaded = {asset["name"]: asset.get("digest") for asset in release["assets"]}
    for name in [*manifest["assets"], "release.json", "SHA256SUMS"]:
        if uploaded.get(name) != "sha256:" + sha256(directory / name):
            raise ValueError("Published asset changed: " + name)
    if args.publish:
        if args.output.exists():
            raise ValueError("Use a fresh AUR checkout")
        run(
            "git",
            "clone",
            f"ssh://aur@aur.archlinux.org/{manifest_name(manifest)}.git",
            args.output,
        )
    stage(args.output, manifest, args.tag)
    if args.publish:
        publish(args.output, manifest)
        print("Published https://aur.archlinux.org/packages/" + manifest_name(manifest))


if __name__ == "__main__":
    main()
