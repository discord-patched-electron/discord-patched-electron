"""Separate immediate client validation from throttled native compilation."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from common import digest, version_key
from fingerprints import integration_key


def date(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Update timestamp must include a timezone")
    return parsed


def in_range(version: str, expression: str) -> bool:
    """GitHub advisory comparator ranges; unknown syntax is never guessed."""
    comparisons = []
    for part in expression.split(","):
        match = re.fullmatch(
            r"\s*(>=|<=|>|<|=)?\s*(\d+\.\d+\.\d+(?:-(?:alpha|beta)\.\d+)?)\s*", part
        )
        if not match:
            raise ValueError("Unsupported advisory version range")
        operator, bound = match.groups()
        a, b = version_key(version), version_key(bound)
        comparisons.append(
            {"<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b, "=": a == b}[operator or "="]
        )
    return all(comparisons)


def urgent(candidate: str, current: str, advisories: list, policy: dict) -> list[str]:
    reasons = ["maintainer-override"] if candidate in policy["urgent_versions"] else []
    for advisory in advisories:
        if advisory.get("severity") not in policy["security_severities"] or advisory.get(
            "withdrawn_at"
        ):
            continue
        for item in advisory.get("vulnerabilities", []):
            if item.get("package") != {"ecosystem": "npm", "name": "electron"}:
                continue
            try:
                fixed = item.get("patched_versions", "")
                # Do not infer that crossing a major boundary contains a backport.
                if (
                    fixed.split(".")[0] == candidate.split(".")[0]
                    and version_key(candidate) >= version_key(fixed)
                    and in_range(current, item["vulnerable_version_range"])
                    and not in_range(candidate, item["vulnerable_version_range"])
                ):
                    reasons.append(advisory["ghsa_id"])
            except (ValueError, TypeError, KeyError):
                continue
    return sorted(set(reasons))


def plan(state: dict, policy: dict, sources: dict, now: str) -> dict:
    good = policy["last_good_runtime"]
    last_attempt = state.get("last_compiler_attempt_at", good["completed_at"])
    due = date(last_attempt) + timedelta(hours=policy["electron_interval_hours"])
    available = [
        r
        for r in state.get("releases", {}).values()
        if r.get("status") == "available"
        and r["channel"] == "prerelease"
        and version_key(r["version"]) > version_key(good["version"])
    ]
    candidate = max(available, key=lambda r: version_key(r["version"])) if available else None
    electron = {"action": "unchanged", "next_routine_at": due.isoformat()}
    if candidate:
        reasons = urgent(
            candidate["version"], good["version"], state.get("security_advisories", []), policy
        )
        spec = {k: candidate[k] for k in ("version", "commit")}
        runtime = state.get("runtimes", {}).get(candidate["runtime_key"], {})
        action = (
            "already-attempted"
            if runtime.get("status") in ("building", "compiled", "failed")
            else "prepare-source"
            if date(now) >= due or reasons
            else "deferred"
        )
        electron.update(
            action=action,
            target=spec,
            runtime_key=candidate["runtime_key"],
            urgent_reasons=reasons,
            source_prepared=sources["targets"]["prerelease"] == spec,
        )
    clients = []
    for channel, client in state["clients"].items():
        if channel in state.get("client_errors", {}):
            continue
        key = integration_key(good["runtime_key"], client, "prerelease")
        work = state.get("client_work", {}).get(key)
        if not work:
            clients.append(
                {
                    "channel": channel,
                    "key": key,
                    "client_digest": digest(client),
                    "reference_run": good["run"],
                }
            )
    return {
        "schema": 1,
        "electron": electron,
        "clients": clients,
        "bundled_runtime_changes": [
            ch
            for ch, r in state.get("bundled_runtimes", {}).items()
            if r.get("changed_since_previous_core")
        ],
        "compiler_provider": "self-hosted",
    }
