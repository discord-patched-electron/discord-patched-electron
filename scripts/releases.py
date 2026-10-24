"""Publish Canary test packages and promote the same assets after manual approval."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

from common import ROOT, digest, read_json, run, sha256, version_key, write_json
from fetch_client_snapshot import fetch as fetch_clients
from fetch_reference import fetch as fetch_runtime
from fingerprints import integration_key

PROJECT = "discord-patched-electron/discord-patched-electron"
URL = "https://github.com/" + PROJECT
PENDING = "**I haven't tested and approved this build yet.**"
FORMATS = (".pkg.tar.zst", ".deb", ".rpm", ".tar.gz")


def api(endpoint, *, method="GET", data=None, missing=False):
    command = ["gh", "api", "repos/" + PROJECT + "/" + endpoint, "--method", method]
    if data is not None:
        command += ["--input", "-"]
    result = subprocess.run(
        command,
        input=json.dumps(data) if data is not None else None,
        capture_output=True,
        text=True,
    )
    if missing and result.returncode and "HTTP 404" in result.stderr:
        return None
    result.check_returncode()
    return json.loads(result.stdout) if result.stdout.strip() else None


def release_tag(client_version, key):
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", client_version) or not re.fullmatch(
        r"[a-f0-9]{64}", key
    ):
        raise ValueError("Invalid release identity")
    return f"canary-{client_version}-{key[:16]}"


def prepare():
    state = os.environ.get("CLIENTS_STATE") or api("git/ref/heads/ci-state")["object"]["sha"]
    runtime = (
        os.environ.get("RUNTIME")
        or read_json(ROOT / "config/update-policy.json")["last_good_runtime"]["run"]
    )
    if not re.fullmatch(r"[1-9][0-9]*", runtime):
        raise ValueError("Expected a compiler run ID")
    fetch_clients(state, ROOT / "out/release-clients.json")
    client = read_json(ROOT / "out/release-clients.json")["clients"]["canary"]
    key = integration_key(runtime, client, "prerelease")
    tag = release_tag(client["version"], key)
    existing = api("releases/tags/" + tag, missing=True)
    # Run creation time survives retries. A partial draft also retains its version.
    timestamp = (existing or api("actions/runs/" + os.environ["GITHUB_RUN_ID"]))["created_at"]
    now = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    values = {
        "state": state,
        "runtime": runtime,
        "key": key,
        "tag": tag,
        "version": now.strftime("%Y%m%d.%H%M%S"),
        "epoch": str(int(now.timestamp())),
        "needed": "false" if existing and not existing["draft"] else "true",
    }
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        for name, value in values.items():
            output.write(f"{name}={value}\n")
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
        summary.write(
            f"Canary {client['version']}: "
            + ("already published" if values["needed"] == "false" else "preparing test packages")
            + f".\n\n[{tag}]({URL}/releases/tag/{tag})\n"
        )


def verify_assets(directory: Path, manifest: dict):
    assets = manifest.get("assets", {})
    if len(assets) != len(FORMATS) or any(
        sum(name.endswith(ext) for name in assets) != 1 for ext in FORMATS
    ):
        raise ValueError("Expected exactly four app package formats")
    for name, expected in assets.items():
        if Path(name).name != name or not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise ValueError("Invalid asset name or checksum")
        if sha256(directory / name) != expected:
            raise ValueError(f"Release checksum mismatch: {name}")
    if manifest.get("source_dirty") is not False or manifest.get("channel") != "canary":
        raise ValueError("Only clean Canary builds can be published or approved")
    if not re.fullmatch(r"[a-f0-9]{40}", manifest.get("source_commit", "")):
        raise ValueError("Missing immutable source commit")


def notes(manifest: dict, previous: str | None):
    commit = manifest["source_commit"]
    revision = f"{previous}..{commit}" if previous else commit
    log = subprocess.check_output(
        ["git", "log", "--format=%H%x09%s", "--max-count=60", revision], cwd=ROOT, text=True
    )
    changes = []
    for entry in log.splitlines():
        sha, subject = entry.split("\t", 1)
        subject = re.sub(r"([\\`*_\[\]<>])", r"\\\1", subject).replace("@", "＠")
        changes.append(f"- {subject} ([{sha[:7]}]({URL}/commit/{sha}))")
    package = next(name for name in manifest["assets"] if name.endswith(".pkg.tar.zst"))
    comparison = (
        f"\n\n[Full changelog]({URL}/compare/{previous}...{commit})"
        if previous
        else f"\n\n[Commit history]({URL}/commits/{commit})"
    )
    return (
        f"{PENDING} This is a public test build. Once I approve it, these same files become the release.\n\n"
        f"- Discord Canary **{manifest['client_version']}**\n"
        f"- Electron **{manifest['electron_version']}**\n"
        f"- Requires **x86-64-v3 (AVX2)** and glibc **{manifest['minimum_glibc']}** or newer.\n\n"
        "## Install\n\n"
        "Choose `.pkg.tar.zst` for Arch, `.deb` for Debian/Ubuntu, `.rpm` for Fedora, or `.tar.gz` for a portable app.\n\n"
        f"```sh\nsudo pacman -U ./{package}\n```\n\n"
        f"[Installation, checksums and updates]({URL}/blob/main/docs/install.md). "
        "Uninstall Vencord or Equicord before updating, then reinstall it.\n\n"
        "## Checks\n\n"
        "Passed plain and Equicord launch/relaunch, native video/Krisp and isolated X11/Wayland audio checks. "
        "Debian installation and launch passed; Arch and Fedora package installation and runtime loading passed. "
        "Live Discord calls, physical camera behavior and full GNOME/KDE sessions need manual testing.\n\n"
        f"[Test results]({URL}/actions/runs/{manifest['workflow_run']}) · "
        f"[Test and approve a release]({URL}/blob/main/docs/agent/releases.md#test-and-approve)\n\n"
        "## Changes\n\n"
        + ("\n".join(changes) or "Discord or Electron inputs updated; no project commits changed.")
        + comparison
        + "\n"
    )


def cache_runtime(manifest: dict):
    runtime = manifest["runtime"]
    tag = runtime["cache_release"]
    cached = api("releases/tags/" + tag, missing=True)
    if cached and not cached["draft"]:
        return
    output = ROOT / "out/release-runtime-cache"
    fetch_runtime(runtime["run"], output)
    capacity = read_json(output / "capacity/capacity.json")
    if (
        digest(capacity) != runtime["capacity_sha256"]
        or sha256(output / "binary/runtime-dist.zip") != runtime["archive_sha256"]
    ):
        raise ValueError("Runtime cache differs from the tested package")
    if not cached:
        run(
            "gh",
            "release",
            "create",
            tag,
            "--repo",
            PROJECT,
            "--draft",
            "--prerelease",
            "--latest=false",
            "--target",
            runtime["compiler_commit"],
            "--title",
            "Build cache: Electron " + runtime["version"],
            "--notes",
            "Electron compiler cache for CI. For the complete Canary app, choose a Canary release. App releases are marked Latest after I test and approve them.",
        )
    run(
        "gh",
        "release",
        "upload",
        tag,
        output / "capacity/capacity.json",
        output / "binary/runtime-dist.zip",
        "--repo",
        PROJECT,
        "--clobber",
    )
    run(
        "gh",
        "release",
        "edit",
        tag,
        "--repo",
        PROJECT,
        "--draft=false",
        "--prerelease",
        "--latest=false",
    )


def publish(directory: Path):
    manifest = read_json(directory / "release.json")
    verify_assets(directory, manifest)
    tag = release_tag(manifest["client_version"], manifest["release_key"])
    existing = api("releases/tags/" + tag, missing=True)
    if existing and not existing["draft"]:
        return
    cache_runtime(manifest)
    previous_releases = [
        release
        for release in api("releases?per_page=100")
        if not release["draft"]
        and release["tag_name"].startswith("canary-")
        and release["tag_name"] != tag
    ]
    previous = (
        max(previous_releases, key=lambda release: release["published_at"])["tag_name"]
        if previous_releases
        else None
    )
    body = directory.parent / "release-notes.md"
    body.write_text(notes(manifest, previous))
    title = f"Canary {manifest['client_version']} · Electron {manifest['electron_version']} · {manifest['version']}"
    if not existing:
        run(
            "gh",
            "release",
            "create",
            tag,
            "--repo",
            PROJECT,
            "--target",
            manifest["source_commit"],
            "--title",
            title,
            "--notes-file",
            body,
            "--draft",
            "--prerelease",
            "--latest=false",
        )
    run(
        "gh",
        "release",
        "upload",
        tag,
        *[directory / name for name in [*manifest["assets"], "release.json", "SHA256SUMS"]],
        "--repo",
        PROJECT,
        "--clobber",
    )
    # Publish only after every asset is uploaded, so notifications link to a complete release.
    run(
        "gh",
        "release",
        "edit",
        tag,
        "--repo",
        PROJECT,
        "--draft=false",
        "--prerelease",
        "--latest=false",
        "--title",
        title,
        "--notes-file",
        body,
    )
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
        summary.write(
            f"[Download and test {title}]({URL}/releases/tag/{tag})\n\nAfter testing, run **Approve tested release** with `{tag}`.\n"
        )


def validate_approval(release: dict, manifest: dict, workflow: dict, approved: dict | None):
    if release["draft"] or release["tag_name"] != release_tag(
        manifest["client_version"], manifest["release_key"]
    ):
        raise ValueError("Select a published Canary candidate")
    if (
        workflow["conclusion"] != "success"
        or workflow["status"] != "completed"
        or workflow["head_branch"] != "main"
        or workflow["head_repository"]["full_name"] != PROJECT
        or workflow["head_sha"] != manifest["source_commit"]
        or workflow["path"] not in (".github/workflows/release.yml", ".github/workflows/update.yml")
    ):
        raise ValueError("Candidate does not come from a successful main-branch release workflow")
    if (
        approved
        and approved["tag"] != release["tag_name"]
        and tuple(map(int, manifest["version"].split(".")))
        <= tuple(map(int, approved["version"].split(".")))
    ):
        raise ValueError(
            "An equal or newer release is already approved; refusing an accidental rollback"
        )


def approve(tag: str, *, check_only=False):
    if not re.fullmatch(r"canary-[0-9]+\.[0-9]+\.[0-9]+-[a-f0-9]{16}", tag):
        raise ValueError("Use a complete Canary release tag")
    release = api("releases/tags/" + tag)
    directory = ROOT / "out/approve-release"
    run("gh", "release", "download", tag, "--repo", PROJECT, "--dir", directory)
    manifest = read_json(directory / "release.json")
    verify_assets(directory, manifest)
    approved_path = ROOT / "config/approved-release.json"
    approved = read_json(approved_path) if approved_path.exists() else None
    validate_approval(release, manifest, api("actions/runs/" + manifest["workflow_run"]), approved)
    tagged = api("commits/" + tag)
    if tagged["sha"] != manifest["source_commit"]:
        raise ValueError("Release tag moved away from the tested commit")
    # Verify the published hashes against GitHub's own upload digests as well.
    uploaded = {asset["name"]: asset.get("digest") for asset in release["assets"]}
    for name in [*manifest["assets"], "release.json", "SHA256SUMS"]:
        if uploaded.get(name) != "sha256:" + sha256(directory / name):
            raise ValueError("Published asset identity changed: " + name)
    policy_path = ROOT / "config/update-policy.json"
    policy = read_json(policy_path)
    runtime = manifest["runtime"]
    if version_key(runtime["version"]) < version_key(policy["last_good_runtime"]["version"]):
        raise ValueError("Candidate would roll back the approved Electron runtime")
    cached = api("releases/tags/" + runtime["cache_release"])
    if cached["draft"]:
        raise ValueError("Runtime cache is not published")
    assets = {asset["name"]: asset.get("digest") for asset in cached["assets"]}
    if assets.get("runtime-dist.zip") != "sha256:" + runtime["archive_sha256"]:
        raise ValueError("Retained runtime differs from tested runtime")
    run(
        "gh",
        "release",
        "download",
        runtime["cache_release"],
        "--repo",
        PROJECT,
        "--pattern",
        "capacity.json",
        "--dir",
        directory / "runtime",
    )
    if digest(read_json(directory / "runtime/capacity.json")) != runtime["capacity_sha256"]:
        raise ValueError("Retained compiler report differs from tested report")
    compiler = api("actions/runs/" + runtime["run"])
    if compiler["conclusion"] != "success" or compiler["head_sha"] != runtime["compiler_commit"]:
        raise ValueError("Compiler provenance changed")
    if check_only:
        print("Approval checks passed. Release still needs manual testing and approval.")
        return
    policy["last_good_runtime"] = {**runtime, "completed_at": compiler["updated_at"]}
    write_json(policy_path, policy)
    write_json(
        approved_path,
        {
            "schema": 1,
            "tag": tag,
            "version": manifest["version"],
            "manifest_sha256": sha256(directory / "release.json"),
            "approved_by": os.environ["GITHUB_ACTOR"],
        },
    )
    run("git", "add", "--", policy_path, approved_path)
    if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode:
        run(
            "git",
            "-c",
            "user.name=github-actions[bot]",
            "-c",
            "user.email=41898282+github-actions[bot]@users.noreply.github.com",
            "commit",
            "-m",
            f"Approve tested Canary release {manifest['version']}",
        )
        run(
            "git",
            "-c",
            "credential.helper=",
            "-c",
            "credential.helper=!gh auth git-credential",
            "push",
            "origin",
            "HEAD:refs/heads/main",
        )
    # This is the final step: no package is rebuilt or replaced during approval.
    api(
        "releases/" + str(release["id"]),
        method="PATCH",
        data={
            "prerelease": False,
            "make_latest": "true",
            "body": release["body"]
            .replace("**Awaiting maintainer testing.**", PENDING, 1)
            .replace(PENDING, "**I've tested and approved this build.**", 1),
        },
    )
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
        summary.write(
            f"[Approved {tag}]({URL}/releases/tag/{tag}). Package files and checksums are unchanged.\n"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "publish", "verify", "approve"])
    parser.add_argument("value", nargs="?")
    args = parser.parse_args()
    if (
        os.environ.get("GITHUB_REPOSITORY") != PROJECT
        or os.environ.get("GITHUB_REF") != "refs/heads/main"
    ):
        raise ValueError("Release writes are restricted to the project main branch")
    if args.action == "prepare":
        prepare()
    elif args.action == "publish":
        publish(Path(args.value))
    else:
        approve(args.value, check_only=args.action == "verify")


if __name__ == "__main__":
    main()
