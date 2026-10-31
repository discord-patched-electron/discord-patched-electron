"""Discover every eligible release into durable state without allocating a compiler."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from build_reference import build_config
from channels import enabled_discord_channels
from common import (
    ROOT,
    digest,
    download,
    electron_channel,
    enabled_electron_channels,
    read_json,
    version_key,
    write_json,
)
from fingerprints import runtime_key
from inspect_bundled_runtime import inspect_clients
from lock_clients import normalize
from update_policy import plan


def release_repositories() -> tuple[str, ...]:
    channels = enabled_electron_channels()
    return tuple(
        repository
        for repository, enabled in (
            ("electron/electron", any(channel != "nightly" for channel in channels)),
            ("electron/nightlies", "nightly" in channels),
        )
        if enabled
    )


class GitHub:
    def get(self, endpoint: str):
        for attempt in range(3):
            try:
                result = subprocess.run(
                    ["gh", "api", endpoint], capture_output=True, text=True, timeout=60
                )
            except subprocess.TimeoutExpired:
                if attempt == 2:
                    raise
            else:
                if result.returncode == 0:
                    return json.loads(result.stdout)
                if not re.search(r"HTTP (429|5\d\d)", result.stderr) or attempt == 2:
                    raise RuntimeError(
                        f"GitHub request failed: {endpoint}: {result.stderr.strip()}"
                    )
            time.sleep(2**attempt)

    def releases(self, repository: str) -> list[dict]:
        records = []
        page = 1
        while True:
            batch = self.get(f"repos/{repository}/releases?per_page=100&page={page}")
            if not isinstance(batch, list):
                raise ValueError("Expected a release page")
            records.extend(
                {
                    **{
                        name: item[name]
                        for name in ("id", "tag_name", "draft", "prerelease", "published_at")
                    },
                    "repository": repository,
                }
                for item in batch
            )
            if len(batch) < 100:
                return records
            page += 1

    def source_tags(self) -> dict[str, str]:
        # A single advertisement also rechecks already-tracked tags. Asking the
        # API separately for every historical tag would grow without bound.
        result = subprocess.check_output(
            ["git", "ls-remote", "--tags", "https://github.com/electron/electron.git"],
            text=True,
            timeout=120,
        )
        return parse_tags(result)

    def verify_commit(self, commit: str):
        try:
            result = self.get(f"repos/electron/electron/git/commits/{commit}")
        except RuntimeError as error:
            if "HTTP 404" in str(error):
                raise ValueError("Source tag did not resolve to an available commit") from error
            raise
        if result.get("sha") != commit or not result.get("tree", {}).get("sha"):
            raise ValueError("Source tag did not resolve to a commit")


def parse_tags(advertisement: str) -> dict[str, str]:
    direct, peeled = {}, {}
    for line in advertisement.splitlines():
        sha, ref = line.split("\t", 1)
        if not re.fullmatch(r"[a-f0-9]{40}", sha) or not ref.startswith("refs/tags/"):
            raise ValueError("Malformed source tag advertisement")
        name = ref.removeprefix("refs/tags/")
        (peeled if name.endswith("^{}") else direct)[name.removesuffix("^{}")] = sha
    return {**direct, **peeled}


def timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Release timestamps must include a timezone")
    return result


def normalize_release(record: dict) -> dict:
    tag = record["tag_name"]
    if not tag.startswith("v"):
        raise ValueError("Source release must use a version tag")
    version = tag[1:]
    channel = electron_channel(version)
    expected_repository = "electron/nightlies" if channel == "nightly" else "electron/electron"
    if record["repository"] != expected_repository or record["prerelease"] is not (
        channel != "stable"
    ):
        raise ValueError("Release repository/metadata does not match its version channel")
    if type(record["id"]) is not int or record["id"] <= 0:
        raise ValueError("Invalid release identity")
    timestamp(record["published_at"])
    return {
        "version": version,
        "channel": channel,
        "tag": tag,
        "repository": record["repository"],
        "release_id": record["id"],
        "published_at": record["published_at"],
    }


def reconcile(
    previous: dict | None,
    releases: list[dict],
    tags: dict[str, str],
    clients: dict,
    sources: dict,
    now: str,
    verify_commit,
) -> dict:
    """No writes or compiler allocation. An unchanged poll returns identical data."""
    if previous is not None and previous.get("schema") != 1:
        raise ValueError("Unsupported discovery state schema")
    state = (
        copy.deepcopy(previous)
        if previous is not None
        else {"schema": 1, "activated_at": now, "releases": {}, "runtimes": {}, "issues": {}}
    )
    baseline = timestamp(state["activated_at"])
    channels = enabled_electron_channels()
    state["enabled_electron_channels"] = list(channels)
    # Preserve completed/running work and its provenance. Only undispatched
    # jobs are retired; pending() also filters old state before migration.
    for runtime in state["runtimes"].values():
        if runtime["channel"] not in channels and runtime["status"] == "pending":
            runtime.update(status="disabled", reason="electron-channel-disabled")
    eligible = {}
    seen_ids = {}
    for record in releases:
        if record["draft"] or not record["published_at"]:
            continue
        try:
            release = normalize_release(record)
        except ValueError as error:
            if timestamp(record["published_at"]) >= baseline:
                identity = record["repository"] + ":" + str(record["id"])
                state["issues"][identity] = {"release": record, "reason": str(error)}
            continue
        if release["channel"] not in channels:
            continue
        identity = release["channel"] + ":" + release["version"]
        if identity in eligible and eligible[identity] != release:
            raise ValueError("Conflicting release records for the same version")
        release_id = (release["repository"], release["release_id"])
        if release_id in seen_ids and seen_ids[release_id] != identity:
            raise ValueError("A release ID changed tags during pagination; retry the snapshot")
        seen_ids[release_id] = identity
        eligible[identity] = release
    previous_ids = {
        (item["repository"], item["release_id"]): identity
        for identity, item in state["releases"].items()
    }
    seeds = set()
    for channel in channels:
        # An initial feed outage still persists client state. Seed the first
        # successful observation of each channel, not just the first state file.
        # Even withdrawn/rejected records count: never backfill their history.
        if any(item["channel"] == channel for item in state["releases"].values()):
            continue
        candidates = [(key, item) for key, item in eligible.items() if item["channel"] == channel]
        if not candidates:
            raise ValueError(f"No published seed for {channel}")
        seeds.add(max(candidates, key=lambda pair: version_key(pair[1]["version"]))[0])
    for identity, release in sorted(eligible.items()):
        previous_identity = previous_ids.get((release["repository"], release["release_id"]))
        if previous_identity and previous_identity != identity:
            state["releases"][previous_identity].update(
                status="release-replaced", observed_tag=release["tag"]
            )
            continue
        old = state["releases"].get(identity)
        if old is None and identity not in seeds and timestamp(release["published_at"]) < baseline:
            continue
        commit = tags.get(release["tag"])
        if old is None:
            old = {**release, "commit": None, "status": "pending-source"}
            state["releases"][identity] = old
        if old["release_id"] != release["release_id"] or old["repository"] != release["repository"]:
            old.update(status="release-replaced", observed_release_id=release["release_id"])
            continue
        if old["status"] in ("tag-moved", "release-replaced"):
            # A later restoration must not silently erase this anomaly.
            continue
        if commit is None:
            old["status"] = "source-tag-missing"
            continue
        if old["commit"] is not None and commit != old["commit"]:
            old.update(status="tag-moved", observed_commit=commit)
            continue
        if old["commit"] is None:
            try:
                verify_commit(commit)
            except ValueError as error:
                old.update(status="invalid-source", reason=str(error))
                continue
            old["commit"] = commit
        old.pop("reason", None)
        old["status"] = "available"
    for identity, release in state["releases"].items():
        if release["channel"] not in channels:
            continue
        if identity not in eligible and release["status"] not in ("tag-moved", "release-replaced"):
            release["status"] = "withdrawn"
    for release in state["releases"].values():
        if release["channel"] not in channels or release["status"] != "available":
            continue
        spec = {name: release[name] for name in ("version", "commit")}
        key = runtime_key(spec, sources, build_config(release["channel"]))
        old_key = release.get("runtime_key")
        if old_key and old_key != key and state["runtimes"][old_key]["status"] == "pending":
            state["runtimes"][old_key].update(
                status="superseded", reason="native-inputs-changed", superseded_by=key
            )
        release["runtime_key"] = key
        state["runtimes"].setdefault(
            key,
            {"target": spec, "channel": release["channel"], "status": "pending", "attempts": []},
        )
    # Preserve every release identity, but compile only the newest pending
    # prerelease. Never cancel a compiler or discard completed/failed evidence.
    candidates = [
        key for key in pending(state) if state["runtimes"][key]["channel"] == "prerelease"
    ]
    if candidates:
        newest = max(
            candidates, key=lambda key: version_key(state["runtimes"][key]["target"]["version"])
        )
        for key in candidates:
            if key != newest:
                state["runtimes"][key].update(
                    status="superseded", reason="newer-prerelease", superseded_by=newest
                )
    if not set(enabled_discord_channels()).issubset(clients):
        raise ValueError("Discovery requires every enabled Discord manifest")
    state["clients"] = {ch: clients[ch] for ch in enabled_discord_channels()}
    return state


def pending(state: dict) -> list[str]:
    channels = enabled_electron_channels()
    available = {
        release["runtime_key"]
        for release in state["releases"].values()
        if release["channel"] in channels
        and release["status"] == "available"
        and release.get("runtime_key")
    }
    priority = {"stable": 0, "prerelease": 1, "nightly": 2}
    return sorted(
        (key for key in available if state["runtimes"][key]["status"] == "pending"),
        key=lambda key: (
            priority[state["runtimes"][key]["channel"]],
            version_key(state["runtimes"][key]["target"]["version"]),
        ),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "out/discovery")
    args = parser.parse_args()
    now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    github = GitHub()
    previous = read_json(args.state) if args.state.exists() else None
    fallback = (
        previous["clients"] if previous else read_json(ROOT / "config/clients.lock.json")["clients"]
    )
    clients, client_errors = {}, {}

    def client_manifest(channel):
        path = download(
            f"https://updates.discord.com/distributions/app/manifests/latest?channel={channel}&platform=linux&arch=x64",
            args.output / (channel + "-manifest.json"),
        )
        return normalize(channel, read_json(path))

    with ThreadPoolExecutor(max_workers=len(enabled_discord_channels())) as pool:
        futures = {
            channel: pool.submit(client_manifest, channel) for channel in enabled_discord_channels()
        }
        for channel, future in futures.items():
            try:
                clients[channel] = future.result()
            except (ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
                clients[channel] = fallback[channel]
                client_errors[channel] = (
                    str(error) if isinstance(error, ValueError) else type(error).__name__
                )
    sources = read_json(ROOT / "config/sources.lock.json")
    try:
        releases = [
            record
            for repository in release_repositories()
            for record in github.releases(repository)
        ]
        state = reconcile(
            previous, releases, github.source_tags(), clients, sources, now, github.verify_commit
        )
        state.pop("electron_discovery_error", None)
    except (ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
        # GitHub outages must not block Discord manifest updates.
        state = (
            copy.deepcopy(previous)
            if previous
            else {"schema": 1, "activated_at": now, "releases": {}, "runtimes": {}, "issues": {}}
        )
        state["clients"] = {ch: clients[ch] for ch in enabled_discord_channels()}
        state["electron_discovery_error"] = str(error)
    state["client_errors"] = client_errors
    try:
        advisories = github.get("repos/electron/electron/security-advisories?per_page=100")
        state["security_advisories"] = [
            {k: a.get(k) for k in ("ghsa_id", "severity", "withdrawn_at", "vulnerabilities")}
            for a in advisories
        ]
        state.pop("security_discovery_error", None)
    except (ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
        state["security_discovery_error"] = str(error)
    inspect_clients(state, args.output / "core-cache")
    policy = read_json(ROOT / "config/update-policy.json")
    good = policy["last_good_runtime"]["runtime_key"]
    if good in state["runtimes"] and state["runtimes"][good]["status"] == "pending":
        state["runtimes"][good]["status"] = "compiled"
    write_json(args.output / "update-plan.json", plan(state, policy, sources, now))
    write_json(args.state, state)
    summary = {
        "changed": state != previous,
        "state_digest": digest(state),
        "tracked_releases": len(state["releases"]),
        "pending_runtime_keys": pending(state),
        "compiler_enabled": read_json(ROOT / "config/channels.json")["compiler"]["enabled"],
        "blocked_releases": {
            key: value["status"]
            for key, value in state["releases"].items()
            if value["status"] != "available"
        },
        "issues": state["issues"],
        "client_errors": client_errors,
    }
    errors = {
        key: state[key]
        for key in (
            "client_errors",
            "bundled_runtime_errors",
            "electron_discovery_error",
            "security_discovery_error",
        )
        if state.get(key)
    }
    summary["errors"] = errors
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a") as stream:
            stream.write(f"errors={str(bool(errors)).lower()}\n")
    write_json(args.output / "discovery.json", summary)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
