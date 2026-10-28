"""Publish test packages and promote the same assets after manual approval."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from channels import require_enabled_discord
from common import (
    DISCORD_CHANNELS,
    ROOT,
    digest,
    electron_channel,
    package_name,
    read_json,
    require_enabled_electron,
    run,
    sha256,
    version_key,
    write_json,
)
from fetch_client_snapshot import fetch as fetch_clients
from fetch_reference import CACHE_REPOSITORY
from fetch_reference import fetch as fetch_runtime
from fingerprints import integration_key
from release_proposals import record_proposal
from release_signing import signature_files
from release_signing import verify as verify_signatures

PROJECT = "discord-patched-electron/discord-patched-electron"
URL = "https://github.com/" + PROJECT
PENDING = "**This build hasn't been approved as stable yet.**"
FORMATS = (".pkg.tar.zst", ".deb", ".rpm", ".tar.gz")


def api(endpoint, *, method="GET", data=None, missing=False, repository=PROJECT):
    command = ["gh", "api", "repos/" + repository + "/" + endpoint, "--method", method]
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


def release_tag(client_version, key, channel="canary", version=None, *, tag_schema=3):
    if (
        channel not in DISCORD_CHANNELS
        or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", client_version)
        or not re.fullmatch(r"[a-f0-9]{64}", key)
    ):
        raise ValueError("Invalid release identity")
    if version is not None:
        if not re.fullmatch(r"[0-9]{8}\.[0-9]{6}", version):
            raise ValueError("Invalid release version")
        datetime.strptime(version, "%Y%m%d.%H%M%S")
        if tag_schema == 3:
            return f"v{version.replace('.', '')}.0.0-{channel}.{key[:16]}"
        if tag_schema == 2:
            date, time = map(int, version.split("."))
            return f"v{date}.{time}.0-{channel}.{key[:16]}"
        raise ValueError("Unsupported release tag schema")
    return f"{channel}-{client_version}-{key[:16]}"


def manifest_tag(manifest):
    schema = manifest.get("tag_schema", 1)
    if schema not in (1, 2, 3):
        raise ValueError("Unsupported release tag schema")
    return release_tag(
        manifest["client_version"],
        manifest["release_key"],
        manifest["channel"],
        manifest["version"] if schema in (2, 3) else None,
        tag_schema=schema,
    )


def existing_candidate(client_version, key, channel):
    legacy = api("releases/tags/" + release_tag(client_version, key, channel), missing=True)
    if legacy:
        return legacy
    pattern = rf"v(?:[0-9]{{14}}\.0|[0-9]{{8}}\.[0-9]{{1,6}})\.0-{channel}\.{key[:16]}"
    page = 1
    while releases := api(f"releases?per_page=100&page={page}"):
        for release in releases:
            if re.fullmatch(pattern, release["tag_name"]):
                return release
        page += 1
    return None


def prepare():
    channel = os.environ.get("CHANNEL", "canary")
    require_enabled_discord(channel)
    state = os.environ.get("CLIENTS_STATE") or api("git/ref/heads/ci-state")["object"]["sha"]
    runtime = (
        os.environ.get("RUNTIME")
        or read_json(ROOT / "config/update-policy.json")["last_good_runtime"]["run"]
    )
    if not re.fullmatch(r"[1-9][0-9]*", runtime):
        raise ValueError("Expected a compiler run ID")
    fetch_clients(state, ROOT / "out/release-clients.json")
    client = read_json(ROOT / "out/release-clients.json")["clients"][channel]
    capacity = fetch_runtime(runtime, ROOT / "out/release-capacity", capacity_only=True)
    electron = capacity["source"]["target"]
    require_enabled_electron(electron)
    key = integration_key(runtime, client, electron)
    existing = existing_candidate(client["version"], key, channel)
    # Run creation time survives retries. A partial draft also retains its version.
    timestamp = (existing or api("actions/runs/" + os.environ["GITHUB_RUN_ID"]))["created_at"]
    now = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if existing and existing["tag_name"].startswith("v"):
        date, time, _ = existing["tag_name"][1:].split(".", 2)
        stamp = date if len(date) == 14 else f"{int(date):08d}{int(time):06d}"
        now = datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    version = now.strftime("%Y%m%d.%H%M%S")
    tag = (
        existing["tag_name"] if existing else release_tag(client["version"], key, channel, version)
    )
    values = {
        "state": state,
        "runtime": runtime,
        "electron": electron,
        "key": key,
        "tag": tag,
        "version": version,
        "epoch": str(int(now.timestamp())),
        "needed": "false" if existing and not existing["draft"] else "true",
    }
    if values["needed"] == "true" and os.environ.get("PUBLISH") == "true":
        reserve_tag(tag, os.environ["GITHUB_SHA"])
        # Reserve the tag before advancing main: GITHUB_TOKEN cannot create a
        # tag for an older workflow-changing commit. This separate metadata
        # commit leaves every qualification job on its original workflow SHA.
        values["proposal_commit"] = record_proposal(
            {
                "schema": 1,
                "tag": tag,
                "version": version,
                "channel": channel,
                "client_version": client["version"],
                "client": client,
                "clients_state": state,
                "source_commit": os.environ["GITHUB_SHA"],
                "electron_version": capacity["compiled_version"],
                "runtime": {
                    "run": runtime,
                    "runtime_key": capacity["runtime_key"],
                    "archive_sha256": capacity["archive_sha256"],
                },
            },
            api,
        )
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        for name, value in values.items():
            output.write(f"{name}={value}\n")
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
        summary.write(
            f"{channel.title()} {client['version']}: "
            + ("already published" if values["needed"] == "false" else "preparing test packages")
            + f".\n\n[{tag}]({URL}/releases/tag/{tag})\n"
        )


def reserve_tag(tag: str, commit: str):
    # Reserve while the workflow commit is still main's tip. Creating a tag for
    # an older workflow-changing commit later can require workflows:write,
    # which GITHUB_TOKEN cannot grant. Never move an existing release tag.
    existing = api("git/ref/tags/" + tag, missing=True)
    if existing:
        if existing["object"]["sha"] != commit:
            raise ValueError(
                "Release tag already pins another commit. Retry its original workflow."
            )
        return
    api("git/refs", method="POST", data={"ref": "refs/tags/" + tag, "sha": commit})


def verify_assets(directory: Path, manifest: dict):
    assets = manifest.get("assets", {})
    application = manifest.get("application")
    if len(assets) != len(FORMATS) + bool(application) or any(
        sum(name.endswith(ext) for name in assets) != 1 for ext in FORMATS
    ):
        raise ValueError("Expected exactly four app package formats")
    if application and (
        application.get("bootstrap_api") != 1
        or application.get("asset") != "application.zip"
        or application.get("archive_sha256") != assets.get("application.zip")
        or application.get("version") != manifest["version"]
        or not re.fullmatch(r"[a-f0-9]{64}", application.get("index_sha256", ""))
    ):
        raise ValueError("Invalid application update asset")
    for name, expected in assets.items():
        if Path(name).name != name or not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise ValueError("Invalid asset name or checksum")
        if sha256(directory / name) != expected:
            raise ValueError(f"Release checksum mismatch: {name}")
    if manifest.get("source_dirty") is not False:
        raise ValueError("Only clean builds can be published or approved")
    require_enabled_discord(manifest.get("channel"))
    require_enabled_electron(electron_channel(manifest["electron_version"]))
    if not re.fullmatch(r"[a-f0-9]{40}", manifest.get("source_commit", "")):
        raise ValueError("Missing immutable source commit")


def install_notes(manifest: dict) -> str:
    tag = manifest_tag(manifest)
    downloads = f"{URL}/releases/download/{tag}"
    name = package_name(manifest["channel"], electron_channel(manifest["electron_version"]))
    rows = [f"| Arch Linux | [AUR](https://aur.archlinux.org/packages/{name}) |"]
    for distribution, extension in (
        ("Debian / Ubuntu", ".deb"),
        ("Fedora", ".rpm"),
        ("Other distributions", ".tar.gz"),
        ("Arch Linux (maintainer/testing install)", ".pkg.tar.zst"),
    ):
        name = next(name for name in manifest["assets"] if name.endswith(extension))
        download = f"[{extension}]({downloads}/{name})"
        if extension == ".pkg.tar.zst" and manifest.get("signing_required"):
            download += f" ([signature]({downloads}/{name}.sig))"
        rows.append(f"| {distribution} | {download} |")
    return (
        "## Install\n\n"
        "| Distribution | Package |\n| --- | --- |\n"
        + "\n".join(rows)
        + f"\n\n[Checksums]({downloads}/SHA256SUMS) · "
        + f"[Installation and updates]({URL}/blob/main/docs/install.md)\n\n"
    )


def app_release_channel(tag: str) -> str | None:
    match = re.fullmatch(
        r"(?:(canary|ptb|stable)-[0-9]+\.[0-9]+\.[0-9]+-|"
        r"v(?:[0-9]{14}\.0|[0-9]{8}\.[0-9]{1,6})\.0-(canary|ptb|stable)\.)"
        r"[a-f0-9]{16}",
        tag,
    )
    return next((group for group in match.groups() if group), None) if match else None


def published_releases() -> list[dict]:
    result = []
    page = 1
    while entries := api(f"releases?per_page=100&page={page}"):
        result.extend(
            entry
            for entry in entries
            if not entry["draft"] and app_release_channel(entry["tag_name"])
        )
        page += 1
    return result


def published_manifest(release: dict) -> dict:
    tag = release["tag_name"]
    if not app_release_channel(tag):
        raise ValueError("Not an app release tag")
    with tempfile.TemporaryDirectory(prefix="release-notes-") as temporary:
        path = Path(temporary)
        run(
            "gh",
            "release",
            "download",
            tag,
            "--repo",
            PROJECT,
            "--pattern",
            "release.json",
            "--dir",
            path,
        )
        manifest = read_json(path / "release.json")
        asset = next(asset for asset in release["assets"] if asset["name"] == "release.json")
        if asset.get("digest") != "sha256:" + sha256(path / "release.json"):
            raise ValueError("Published release manifest checksum mismatch")
    if manifest_tag(manifest) != tag or not re.fullmatch(
        r"[a-f0-9]{40}", manifest["source_commit"]
    ):
        raise ValueError("Published release manifest identity mismatch")
    return manifest


def previous_release(manifest: dict, releases: list[dict], fetch=published_manifest) -> dict | None:
    candidates = []
    for release in releases:
        if (
            release["draft"]
            or app_release_channel(release["tag_name"]) != manifest["channel"]
            or release["tag_name"] == manifest_tag(manifest)
        ):
            continue
        candidate = fetch(release)
        # Build versions establish order even when a delayed job publishes late.
        # Never compare a historical release against a newer candidate.
        if candidate["version"] < manifest["version"] and electron_channel(
            candidate["electron_version"]
        ) == electron_channel(manifest["electron_version"]):
            candidates.append(candidate)
    return max(candidates, key=lambda item: item["version"]) if candidates else None


def input_changes(manifest: dict, previous: dict | None) -> list[str]:
    changes = []
    for field, label in (("client_version", "Discord"), ("electron_version", "Electron")):
        current = manifest[field]
        if previous is None:
            changes.append(f"- {label} **{current}**.")
        elif previous[field] != current:
            changes.append(f"- {label} **{previous[field]} → {current}**.")
        elif field == "electron_version" and previous.get("runtime", {}).get(
            "archive_sha256"
        ) != manifest.get("runtime", {}).get("archive_sha256"):
            changes.append(f"- Electron **{current}** rebuilt; runtime archive changed.")
        else:
            changes.append(f"- {label} **{current}** (unchanged).")
    if previous and previous["client_version"] == manifest["client_version"]:
        before, after = previous.get("client", {}), manifest.get("client", {})
        old, new = before.get("modules", {}), after.get("modules", {})
        changed = sorted(name for name in old.keys() | new.keys() if old.get(name) != new.get(name))
        if changed:
            names = ", ".join(f"`{name}`" for name in changed[:3])
            more = f" (+{len(changed) - 3} more)" if len(changed) > 3 else ""
            changes.append(f"- Discord modules updated: {names}{more}.")
        elif before != after:
            changes.append("- Discord client pins or required modules updated.")
    return changes


def approval_action(manifest: dict) -> str:
    return (
        "\n\n[![Approve this release](https://img.shields.io/badge/Approve_this_release-238636?style=for-the-badge)]"
        f"({URL}/actions/workflows/approve-release.yml)\n\n"
        "**Maintainers:** choose **Run workflow** on `main`, paste "
        f"`{manifest_tag(manifest)}` into **release_tag**, and confirm that you tested this build."
    )


def approved_notes(body: str, manifest: dict) -> str:
    return (
        body.replace(approval_action(manifest), "", 1)
        .replace("**Awaiting maintainer testing.**", PENDING, 1)
        .replace("**I haven't tested and approved this build yet.**", PENDING, 1)
        .replace("**I haven't approved this build as stable yet.**", PENDING, 1)
        .replace(PENDING, "**This build has been approved as stable.**", 1)
    )


def notes(manifest: dict, previous: dict | None, *, approved: bool = False):
    commit = manifest["source_commit"]
    log = ""
    if previous and previous["source_commit"] != commit:
        log = subprocess.check_output(
            [
                "git",
                "log",
                "--first-parent",
                "--format=%H%x09%s",
                "--max-count=30",
                f"{previous['source_commit']}..{commit}",
                "--",
                "integration",
                "scripts",
                "packaging",
                "patches",
                "config",
                ".github",
                "pyproject.toml",
                "uv.lock",
                "package.json",
                "bun.lock",
                ":(exclude)config/update-proposals/**",
                ":(exclude)config/approved*.json",
            ],
            cwd=ROOT,
            text=True,
        )
    changes = input_changes(manifest, previous)
    project_changes = []
    for entry in log.splitlines():
        sha, subject = entry.split("\t", 1)
        if subject.startswith(
            ("Approve tested ", "Propose Discord ", "Update release discovery state")
        ):
            continue
        subject = re.sub(r"([\\`*_\[\]<>])", r"\\\1", subject).replace("@", "＠")
        project_changes.append(f"- {subject} ([{sha[:7]}]({URL}/commit/{sha}))")
    changes.extend(project_changes[:5])
    if len(project_changes) > 5:
        changes.append("- Additional project changes are in the full changelog.")
    if previous is None:
        changes.append("- No earlier published baseline is available for comparison.")
    comparison = (
        f"\n\n[Full changelog]({URL}/compare/{manifest_tag(previous)}...{manifest_tag(manifest)})"
        if previous
        else f"\n\n[Full changelog]({URL}/commits/{commit})"
    )
    proposal = manifest.get("proposal_commit")
    snapshot = manifest.get("clients_state")
    provenance = ""
    if proposal and re.fullmatch(r"[a-f0-9]{40}", proposal):
        provenance = f" · [Update proposal]({URL}/commit/{proposal})"
    elif snapshot and re.fullmatch(r"[a-f0-9]{40}", snapshot):
        provenance = f" · [Client snapshot]({URL}/commit/{snapshot})"
    return (
        ("**This build has been approved as stable.**" if approved else PENDING)
        + ("" if approved else approval_action(manifest))
        + f"\n\nRequires **x86-64-v3 (AVX2)** and glibc **{manifest['minimum_glibc']}** or newer.\n\n"
        + install_notes(manifest)
        + "## Changes\n\n"
        + "\n".join(changes)
        + comparison
        + provenance
        + f" · [✅ Test results]({URL}/actions/runs/{manifest['workflow_run']})"
        + "\n"
    )


def refresh_notes(tag: str | None, *, apply: bool = False):
    releases = published_releases()
    manifests = {}

    def fetch(release):
        key = release["tag_name"]
        if key not in manifests:
            manifests[key] = published_manifest(release)
        return manifests[key]

    targets = [release for release in releases if tag is None or release["tag_name"] == tag]
    if not targets:
        raise ValueError("No matching published app releases")
    output = ROOT / "out/release-notes"
    output.mkdir(parents=True, exist_ok=True)
    for release in targets:
        manifest = fetch(release)
        previous = previous_release(manifest, releases, fetch)
        current = api("releases/" + str(release["id"])) if apply else release
        body = notes(manifest, previous, approved=not current["prerelease"])
        (output / (release["tag_name"] + ".before.md")).write_text(current.get("body") or "")
        (output / (release["tag_name"] + ".md")).write_text(body)
        if apply and body != current.get("body"):
            api("releases/" + str(release["id"]), method="PATCH", data={"body": body})
        print(("Updated" if apply else "Previewed") + " notes: " + release["tag_name"])


def cache_runtime(manifest: dict):
    runtime = manifest["runtime"]
    tag = runtime["cache_release"]
    cached = api("releases/tags/" + tag, missing=True, repository=CACHE_REPOSITORY)
    if cached and not cached["draft"]:
        assets = {asset["name"]: asset.get("digest") for asset in cached["assets"]}
        if assets.get("runtime-dist.zip") != "sha256:" + runtime["archive_sha256"]:
            raise ValueError("Published compiler cache has different bytes")
        return
    output = ROOT / "out/release-runtime-cache"
    fetch_runtime(runtime["run"], output)
    capacity = read_json(output / "capacity/capacity.json")
    if (
        digest(capacity) != runtime["capacity_sha256"]
        or sha256(output / "binary/runtime-dist.zip") != runtime["archive_sha256"]
    ):
        raise ValueError("Runtime cache differs from the tested package")
    token = os.environ.get("CACHE_TOKEN")
    if not token:
        raise ValueError("Set CACHE_TOKEN with Contents write access to " + CACHE_REPOSITORY)
    cache_environment = {**os.environ, "GH_TOKEN": token}
    if not cached:
        run(
            "gh",
            "release",
            "create",
            tag,
            "--repo",
            CACHE_REPOSITORY,
            "--draft",
            "--prerelease",
            "--latest=false",
            "--target",
            "main",
            "--title",
            "Build cache: Electron " + runtime["version"],
            "--notes",
            "Compiler artifacts for CI. Download the app from https://github.com/discord-patched-electron/discord-patched-electron/releases.",
            env=cache_environment,
        )
    run(
        "gh",
        "release",
        "upload",
        tag,
        output / "capacity/capacity.json",
        output / "binary/runtime-dist.zip",
        "--repo",
        CACHE_REPOSITORY,
        "--clobber",
        env=cache_environment,
    )
    run(
        "gh",
        "release",
        "edit",
        tag,
        "--repo",
        CACHE_REPOSITORY,
        "--draft=false",
        "--prerelease",
        "--latest=false",
        env=cache_environment,
    )


def publish(directory: Path):
    manifest = read_json(directory / "release.json")
    verify_assets(directory, manifest)
    verify_signatures(directory)
    tag = manifest_tag(manifest)
    existing = api("releases/tags/" + tag, missing=True)
    if existing and not existing["draft"]:
        return
    cache_runtime(manifest)
    previous = previous_release(manifest, published_releases())
    body = directory.parent / "release-notes.md"
    body.write_text(notes(manifest, previous))
    title = f"Discord {manifest['channel'].title()} {manifest['client_version']} / Electron {manifest['electron_version']} ({manifest['version']})"
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
        *signature_files(directory),
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
    if release["draft"] or release["tag_name"] != manifest_tag(manifest):
        raise ValueError("Select a published test release")
    if (
        workflow["conclusion"] != "success"
        or workflow["status"] != "completed"
        or workflow["head_branch"] != "main"
        or workflow["head_repository"]["full_name"] != PROJECT
        or workflow["head_sha"] != manifest["source_commit"]
        or workflow["path"]
        not in (
            ".github/workflows/release.yml",
            ".github/workflows/update.yml",
            ".github/workflows/release-channels.yml",
        )
    ):
        raise ValueError("Candidate does not come from a successful main-branch release workflow")
    if (
        approved
        and approved["tag"] != release["tag_name"]
        and tuple(map(int, manifest["version"].split(".")))
        <= tuple(map(int, approved["version"].split(".")))
    ):
        raise ValueError(
            "An equal or newer release is already approved. Refusing an accidental rollback."
        )


def approval_path(manifest: dict) -> Path:
    channel = manifest["channel"]
    electron = electron_channel(manifest["electron_version"])
    name = (
        "approved-release.json"
        if (channel, electron) == ("canary", "prerelease")
        else f"approved-{channel}-{electron}-release.json"
    )
    return ROOT / "config" / name


def approve(tag: str, *, check_only=False):
    if not re.fullmatch(
        r"(?:(?:canary|ptb|stable)-[0-9]+\.[0-9]+\.[0-9]+-[a-f0-9]{16}|v(?:[0-9]{14}\.0|[0-9]{8}\.[0-9]{1,6})\.0-(?:canary|ptb|stable)\.[a-f0-9]{16})",
        tag,
    ):
        raise ValueError("Use a complete app release tag")
    release = api("releases/tags/" + tag)
    directory = ROOT / "out/approve-release" / tag
    run("gh", "release", "download", tag, "--repo", PROJECT, "--dir", directory, "--clobber")
    manifest = read_json(directory / "release.json")
    verify_assets(directory, manifest)
    verify_signatures(directory, required=manifest.get("signing_required", False))
    channel = manifest["channel"]
    approved_path = approval_path(manifest)
    approved = read_json(approved_path) if approved_path.exists() else None
    validate_approval(release, manifest, api("actions/runs/" + manifest["workflow_run"]), approved)
    tagged = api("commits/" + tag)
    if tagged["sha"] != manifest["source_commit"]:
        raise ValueError("Release tag moved away from the tested commit")
    # Verify the published hashes against GitHub's own upload digests as well.
    uploaded = {asset["name"]: asset.get("digest") for asset in release["assets"]}
    for name in [
        *manifest["assets"],
        "release.json",
        "SHA256SUMS",
        *[path.name for path in signature_files(directory) if path.exists()],
    ]:
        if uploaded.get(name) != "sha256:" + sha256(directory / name):
            raise ValueError("Published asset identity changed: " + name)
    policy_path = ROOT / "config/update-policy.json"
    policy = read_json(policy_path)
    runtime = manifest["runtime"]
    if version_key(runtime["version"]) < version_key(policy["last_good_runtime"]["version"]):
        raise ValueError("Candidate would roll back the approved Electron runtime")
    cached = api("releases/tags/" + runtime["cache_release"], repository=CACHE_REPOSITORY)
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
        CACHE_REPOSITORY,
        "--pattern",
        "capacity.json",
        "--dir",
        directory / "runtime",
        "--clobber",
    )
    if digest(read_json(directory / "runtime/capacity.json")) != runtime["capacity_sha256"]:
        raise ValueError("Retained compiler report differs from tested report")
    compiler = api("actions/runs/" + runtime["run"])
    if compiler["conclusion"] != "success" or compiler["head_sha"] != runtime["compiler_commit"]:
        raise ValueError("Compiler provenance changed")
    if check_only:
        print("Release integrity and provenance checks passed. Approval status unchanged.")
        return directory
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
            f"Approve tested {channel.title()} release {manifest['version']}",
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
            "body": approved_notes(release["body"], manifest),
        },
    )
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
        summary.write(
            f"[Approved {tag}]({URL}/releases/tag/{tag}). Package files and checksums are unchanged.\n"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["prepare", "publish", "verify", "approve", "refresh-notes"]
    )
    parser.add_argument("value", nargs="?")
    parser.add_argument(
        "--apply", action="store_true", help="Publish refreshed release descriptions"
    )
    args = parser.parse_args()
    if args.apply and args.action != "refresh-notes":
        parser.error("--apply is only supported for refresh-notes")
    if args.action == "refresh-notes" and not args.apply:
        refresh_notes(args.value)
        return
    if (
        os.environ.get("GITHUB_REPOSITORY") != PROJECT
        or os.environ.get("GITHUB_REF") != "refs/heads/main"
    ):
        raise ValueError("Release writes are restricted to the project main branch")
    if args.action == "refresh-notes":
        refresh_notes(args.value, apply=args.apply)
    elif args.action == "prepare":
        prepare()
    elif args.action == "publish":
        publish(Path(args.value))
    else:
        approve(args.value, check_only=args.action == "verify")


if __name__ == "__main__":
    main()
