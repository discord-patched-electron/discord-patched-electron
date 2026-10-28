"""Select push/PR checks from the complete Git diff; uncertain inputs run everything."""

import argparse
import json
import os
import re
import subprocess
from fnmatch import fnmatchcase
from pathlib import Path

SCOPES = ("unit", "source", "native", "audio", "packaging", "compiler", "camera", "launcher")

# First matching rule wins. Keep narrow exceptions above directory rules. Anything
# unlisted (including new shared helpers) gets full coverage until explicitly scoped.
RULES = (
    (("README.md", "CONTRIBUTING.md", "AGENTS.md", "docs/*", ".github/*.md"), ()),
    (("tests/test_*.py", "tests/mod_fixture.py", "tests/*/*.test.cjs"), ("unit",)),
    (
        (
            "scripts/releases.py",
            "scripts/release_proposals.py",
            "scripts/publish_aur.py",
            "scripts/release_signing.py",
            "scripts/discover.py",
            "scripts/schedule_updates.py",
            "scripts/update_policy.py",
            "scripts/state_branch.py",
            "scripts/advance_electron.py",
            "scripts/blacksmith_budget.py",
            "scripts/ci_usage.py",
            "scripts/check_style.py",
            "config/approved-release.json",
            "config/update-policy.json",
            "config/update-proposals/*",
            ".github/workflows/approve-release.yml",
            ".github/workflows/publish-aur.yml",
            ".github/workflows/update.yml",
            ".github/workflows/release.yml",
            ".github/workflows/release-channels.yml",
            ".github/actions/publish-aur/*",
            ".github/actionlint.yaml",
            ".clang-format",
            ".editorconfig",
            ".gitignore",
            ".oxfmtrc.json",
            ".oxlintrc.json",
        ),
        ("unit",),
    ),
    (("ci/arch/*",), ("packaging", "audio")),
    (("ci/audio/*", "scripts/build_test_compositor.sh"), ("audio",)),
    ((".github/workflows/app-audio.yml",), ("audio",)),
    ((".github/workflows/native-runtime.yml",), ("native",)),
    ((".github/workflows/package-check.yml",), ("packaging",)),
    (("tests/audio/speech.*",), ("audio", "native")),
    (("tests/audio/*", "tests/wayland/*", "tests/fixtures/pulse-controller.json"), ("audio",)),
    (
        (
            "tests/runtime/*",
            "scripts/check_runtime_clients.py",
            "scripts/probe_runtime.py",
            "scripts/check_krisp_audio.py",
            "scripts/inspect_bundled_runtime.py",
        ),
        ("native",),
    ),
    (
        (
            "integration/audio/*",
            "scripts/patch_app_audio.py",
            "scripts/patch_pulse_audio.py",
            "scripts/desktop_identity.py",
        ),
        ("packaging", "audio"),
    ),
    (("scripts/check_app_audio.py", "scripts/check_wayland_audio.py"), ("audio",)),
    (
        (
            "scripts/patch_krisp.py",
            "scripts/client_native.py",
            "packaging/upstream/krisp-patcher.py",
        ),
        ("packaging", "native"),
    ),
    (
        (
            "integration/camera/*",
            "scripts/build_camera_support.py",
            "scripts/camera_scope.py",
            "tests/camera/*",
            "scripts/check_camera_startup.py",
        ),
        ("packaging", "camera"),
    ),
    (("integration/launcher/*",), ("packaging", "launcher")),
    (
        (
            "integration/bootstrap/*",
            "integration/updater/*",
            "integration/desktop/*",
            "packaging/linux/*",
            "packaging/PKGBUILD.in",
            "packaging/discord-canary.png",
            "packaging/release-signing-key.asc",
            "packaging/aur-known-hosts",
            "scripts/prepare_app.py",
            "scripts/application_payload.py",
            "scripts/stage_updater.py",
            "scripts/stage_openasar.py",
            "scripts/stage_package.py",
            "scripts/updater_python.py",
            "scripts/patch_desktop_core.py",
            "scripts/generate_package.py",
            "scripts/build_local_app.py",
            "scripts/package_release.py",
            "scripts/check_release_packages.py",
            "scripts/smoke_packages.py",
            "scripts/fingerprints.py",
            "config/openasar.lock.json",
            "THIRD_PARTY.md",
            "LICENSE",
        ),
        ("packaging",),
    ),
    (
        (
            "scripts/compiler_cache.py",
            "scripts/check_compiler_cache.py",
            "config/compiler-cache.json",
        ),
        ("compiler",),
    ),
)


def select(paths: list[str], reason: str = "") -> dict:
    selected = set(SCOPES) if reason else set()
    decisions = {}
    for path in sorted(set(paths)):
        scopes = next(
            (scopes for patterns, scopes in RULES if any(fnmatchcase(path, p) for p in patterns)),
            SCOPES,
        )
        selected.update(scopes)
        decisions[path] = list(scopes)
    if selected:
        selected.add("unit")
    return {
        "checks": {name: name in selected for name in SCOPES},
        "reason": reason or "Selected from changed paths; lint always runs.",
        "paths": decisions,
    }


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True, stderr=subprocess.PIPE)


def changed_paths(repo: Path, base: str, head: str, *, pull_request: bool = False) -> list[str]:
    for commit in (base, head):
        if not re.fullmatch(r"[0-9a-f]{40}", commit) or commit == "0" * 40:
            raise ValueError("Missing or invalid commit; full checks required")
        git(repo, "cat-file", "-e", f"{commit}^{{commit}}")
    if pull_request:
        base = git(repo, "merge-base", base, head).strip()
    # Disabling rename detection includes both old and new paths. NUL separation
    # handles spaces/newlines, and local Git avoids API file-count limits.
    return git(repo, "diff", "--no-renames", "--name-only", "-z", base, head, "--").split("\0")[:-1]


def event_scope(repo: Path, event_name: str, event: dict) -> dict:
    if event_name == "workflow_dispatch":
        return select([], "Manual run: all checks requested.")
    try:
        if event_name == "pull_request":
            pr = event["pull_request"]
            paths = changed_paths(repo, pr["base"]["sha"], pr["head"]["sha"], pull_request=True)
        elif event_name == "push":
            paths = changed_paths(repo, event["before"], event["after"])
        else:
            raise ValueError(f"Unsupported event: {event_name}")
        return select(paths)
    except (KeyError, TypeError, ValueError, OSError, subprocess.CalledProcessError) as error:
        return select([], f"Cannot determine the full diff ({error}); running all checks.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="Compare explicit commit SHAs locally")
    parser.add_argument("--head")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    if args.base or args.head:
        if not (args.base and args.head):
            parser.error("--base and --head must be supplied together")
        result = event_scope(repo, "push", {"before": args.base, "after": args.head})
    else:
        try:
            event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
            result = event_scope(repo, os.environ["GITHUB_EVENT_NAME"], event)
        except (KeyError, ValueError, OSError) as error:
            result = select([], f"Cannot read event ({error}); running all checks.")
    print(json.dumps(result, indent=2))
    if output := os.environ.get("GITHUB_OUTPUT"):
        with Path(output).open("a") as stream:
            for name, enabled in result["checks"].items():
                stream.write(f"{name}={str(enabled).lower()}\n")
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a") as stream:
            stream.write("## Check scope\n\n" + result["reason"] + "\n\n")
            stream.write("| Check | Selected |\n| --- | --- |\n| lint | yes |\n")
            for name, enabled in result["checks"].items():
                stream.write(f"| {name} | {'yes' if enabled else 'no'} |\n")
            stream.write("\nThe selector log lists the scope for each changed path.\n")


if __name__ == "__main__":
    main()
