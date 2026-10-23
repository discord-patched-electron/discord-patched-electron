"""Keep state on an isolated Git branch; never merge generated state into main."""
import argparse
import os
from pathlib import Path
import subprocess

from common import read_json, run

BRANCH = "ci-state"
PROJECT = "discord-patched-electron/discord-patched-electron"


def git(directory: Path, *args, **kwargs):
    # gh reads its credential from the environment. No token in a URL, command
    # argument, repository config or generated state file.
    return subprocess.run(["git", "-C", str(directory), "-c", "credential.helper=",
        "-c", "credential.helper=!gh auth git-credential", *args], **kwargs)


def checkout(remote: str, directory: Path):
    if directory.exists():
        raise ValueError("State checkout requires a fresh directory")
    run("git", "init", "--quiet", "--initial-branch=" + BRANCH, directory)
    git(directory, "remote", "add", "origin", remote, check=True)
    found = git(directory, "ls-remote", "--exit-code", "origin", "refs/heads/" + BRANCH, capture_output=True, text=True)
    if found.returncode == 2:
        return
    found.check_returncode()
    git(directory, "fetch", "--quiet", "--depth=1", "origin", "refs/heads/" + BRANCH, check=True)
    git(directory, "checkout", "--quiet", "-B", BRANCH, "FETCH_HEAD", check=True)


def save(directory: Path) -> bool:
    if read_json(directory / "state.json").get("schema") != 1:
        raise ValueError("Refusing to publish unsupported state")
    branch = git(directory, "symbolic-ref", "--short", "HEAD", capture_output=True, text=True, check=True).stdout.strip()
    if branch != BRANCH:
        raise ValueError("Refusing to write state outside ci-state")
    git(directory, "add", "--", "state.json", check=True)
    diff = git(directory, "diff", "--cached", "--quiet")
    if diff.returncode == 0:
        return False
    if diff.returncode != 1:
        diff.check_returncode()
    git(directory, "-c", "user.name=github-actions[bot]",
        "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
        "commit", "--quiet", "-m", "Update release discovery state", check=True)
    # A concurrent writer causes a failed fast-forward push, never a force or
    # text merge. The next poll checks out fresh state and reconciles again.
    git(directory, "push", "origin", "HEAD:refs/heads/" + BRANCH, check=True)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["checkout", "save"])
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    if os.environ.get("GITHUB_REPOSITORY") != PROJECT:
        raise ValueError("State writes are limited to the project repository")
    if args.action == "checkout":
        checkout("https://github.com/" + PROJECT + ".git", args.directory.resolve())
    else:
        print("State updated" if save(args.directory.resolve()) else "No state change; no commit or push")


if __name__ == "__main__":
    main()
