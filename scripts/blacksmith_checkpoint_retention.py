"""Replace only our main-branch Ninja checkpoints after a new archive is ready.

Two full snapshots can exceed the free 25 GB cache capacity. Delete the old
managed snapshot immediately before uploading its verified replacement, never
before compilation. Unrelated caches and branch scopes are not touched.
"""

import os
import re
import subprocess

from blacksmith_usage import ORGANIZATION, cli
from state_branch import PROJECT

KEY = re.compile(r"ninja-v2-incremental-[a-f0-9]{64}-[a-f0-9]{64}-[0-9]+")


def managed_keys(fetch=cli) -> list[str]:
    keys = set()
    for page in range(1, 101):
        result = fetch(
            "cache",
            "list",
            "--repo",
            PROJECT,
            "--search",
            "ninja-v2-",
            "--page",
            str(page),
            "--per-page",
            "100",
        )
        if result["repo"] != PROJECT or result["installation"]["installation_name"] != ORGANIZATION:
            raise ValueError("Wrong provider cache repository")
        for entry in result["entries"]:
            if entry.get("scope") == "refs/heads/main" and KEY.fullmatch(entry["key"]):
                keys.add(entry["key"])
        pagination = result["pagination"]
        if pagination["current_page"] != page:
            raise ValueError("Cache pagination did not advance")
        if not pagination["has_more_pages"]:
            return sorted(keys)
    raise ValueError("Incomplete cache listing; no entries deleted")


def main():
    if (
        os.environ.get("GITHUB_REPOSITORY") != PROJECT
        or os.environ.get("GITHUB_REF") != "refs/heads/main"
    ):
        raise ValueError("Checkpoint retention is restricted to the project main branch")
    for key in managed_keys():
        subprocess.run(
            [
                os.environ.get("BLACKSMITH_CLI", "blacksmith"),
                "cache",
                "delete",
                "--org",
                ORGANIZATION,
                "--repo",
                PROJECT,
                "--scope",
                "refs/heads/main",
                "--key",
                key,
                "--yes",
            ],
            env={**os.environ, "BLACKSMITH_DISABLE_AUTO_UPDATE": "1"},
            check=True,
            timeout=60,
        )


if __name__ == "__main__":
    main()
