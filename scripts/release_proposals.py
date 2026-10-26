"""Commit exact candidate inputs on main without changing the tested source tree."""

import base64
import json
import re

from common import electron_channel


def record_proposal(proposal: dict, api) -> str:
    channel = proposal["channel"]
    track = electron_channel(proposal["electron_version"])
    if channel not in ("canary", "ptb", "stable"):
        raise ValueError("Invalid proposal channel")
    path = f"config/update-proposals/{channel}-{track}.json"
    # Pin the reads together: another candidate can advance main between calls.
    head = api("git/ref/heads/main")["object"]["sha"]
    previous = api(f"contents/{path}?ref={head}", missing=True)
    if previous:
        saved = json.loads(base64.b64decode(previous["content"]))
        if saved == proposal:
            # Return the commit that last changed this file, not a moving branch.
            return api(f"commits?sha={head}&path={path}&per_page=1")[0]["sha"]
        if saved.get("tag") == proposal["tag"]:
            raise ValueError("Existing proposal has different inputs for the same release tag")
        if saved.get("version", "") > proposal["version"]:
            # Retried older workflows must not replace a newer proposal record.
            # Find their original immutable commit instead.
            page = 1
            while commits := api(f"commits?sha={head}&path={path}&per_page=100&page={page}"):
                for commit in commits:
                    old = api(f"contents/{path}?ref={commit['sha']}")
                    if json.loads(base64.b64decode(old["content"])) == proposal:
                        return commit["sha"]
                page += 1
            raise ValueError("A newer proposal exists; refusing to record an older candidate")
    client = proposal["client_version"]
    electron = proposal["electron_version"]
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", client):
        raise ValueError("Invalid proposed client version")
    message = f"Propose Discord {channel.title()} {client} / Electron {electron}"
    if previous and saved.get("client_version") == client:
        if saved.get("client") != proposal["client"]:
            message += " (module update)"
        elif saved.get("runtime") == proposal["runtime"]:
            message += " (package update)"
    result = api(
        "contents/" + path,
        method="PUT",
        data={
            "branch": "main",
            "message": message,
            "content": base64.b64encode(
                (json.dumps(proposal, indent=2, sort_keys=True) + "\n").encode()
            ).decode(),
            **({"sha": previous["sha"]} if previous else {}),
        },
    )
    return result["commit"]["sha"]
