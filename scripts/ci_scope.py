"""Select push/PR checks from the complete Git diff; uncertain inputs run everything."""

import argparse
import json
import os
import re
import subprocess
from fnmatch import fnmatchcase
from pathlib import Path

SCOPES = (
    "unit",
    "source",
    "native",
    "audio",
    "packaging",
    "compiler",
    "camera",
    "capture",
    "appimage",
)
SUITES = ("python", "javascript", "rust")
PR_INTEGRATIONS = ("source", "audio", "packaging", "appimage")

# These files have independent consumers. Everything else keeps the established
# unit coverage, including source files imported by fixtures in another language.
FOCUSED_SUITES = (
    (("config/release-notes.json",), ("notes",)),
    (("integration/video/kms-README.md", "THIRD_PARTY.md"), ()),
    (
        (
            "scripts/assemble.py",
            "scripts/source_patch.py",
            "tests/test_partial_source_cache.py",
            "tests/test_source_patch.py",
        ),
        ("source_tests",),
    ),
    (
        (
            "config/sources.lock.json",
            "config/ports/*",
            "patches/electron/*",
            "patches/discord/*",
            "patches/ports/*",
        ),
        ("source_tests",),
    ),
    (("tests/*/*.test.cjs",), ("javascript",)),
    (
        (
            "tests/test_release*.py",
            "tests/test_source_verification.py",
            "tests/test_ci_scope.py",
            "tests/test_ci_pr_evidence.py",
            "scripts/releases.py",
            "scripts/source_verification.py",
            "scripts/release_proposals.py",
        ),
        ("release_tests",),
    ),
    (
        (
            ".github/workflows/sync-dev.yml",
            ".github/actionlint.yaml",
            ".editorconfig",
            ".gitignore",
            ".oxfmtrc.json",
            ".oxlintrc.json",
        ),
        (),
    ),
)

# First matching rule wins. Keep narrow exceptions above directory rules. Anything
# unlisted (including new shared helpers) gets full coverage until explicitly scoped.
RULES = (
    (
        (
            "scripts/dev.py",
            "scripts/dev_client.py",
            "scripts/dev_current.py",
            "scripts/source_verification.py",
        ),
        ("unit",),
    ),
    (("scripts/check_graphics.py", "scripts/check_popout.py"), ("unit",)),
    (
        (
            "scripts/check_launch_settings.py",
            "tests/launch-settings/*",
            "scripts/check_application_update.py",
            "scripts/check_client_mods.py",
            ".github/actions/check-client-mods/*",
        ),
        ("packaging",),
    ),
    (("scripts/check_frame_capture.py", "scripts/check_safe_storage.py"), ("native",)),
    (("config/clients.lock.json",), ("native", "audio", "packaging", "appimage")),
    (("config/sources.lock.json", "config/ports/*", "patches/electron/*"), ("source",)),
    (("README*.md", "**/README*.md"), ()),
    (
        ("patches/discord/*", "patches/ports/*", "scripts/assemble.py", "scripts/source_patch.py"),
        ("source",),
    ),
    (
        ("packaging/upstream/*", "scripts/check_installed_package.py", "tests/client-mods/*"),
        ("packaging",),
    ),
    (("tests/frame-capture/*", "tests/safe-storage/*"), ("native",)),
    (
        (
            "tests/graphics/*",
            "tests/popout/*",
            "tests/desktop-preferences/*",
            "tests/external-links/*",
            "tests/middle-click/*",
            "tests/shortcuts/*",
            "tests/spellcheck/*",
        ),
        ("unit",),
    ),
    (("artwork/*.kra", "*.kra", "discord-patched-electron.png"), ()),
    (
        (
            ".github/actions/blacksmith-cli/*",
            ".github/workflows/build.yml",
            ".github/workflows/import-runtime.yml",
            ".github/workflows/sync-dev.yml",
            "ci/gn/*",
            "ci/runner/*",
            "config/build.json",
            "config/candidate-build.json",
            "scripts/build_reference.py",
            "scripts/build_resources.py",
            "scripts/build_metrics.py",
            "scripts/build_checkpoint.py",
            "scripts/blacksmith_checkpoint_retention.py",
            "scripts/import_local_runtime.py",
            "scripts/incremental_workspace.py",
            "scripts/setup_local_runner.py",
            "scripts/runtime_fingerprints.py",
            "scripts/fetch_client_snapshot.py",
            "scripts/fetch_reference.py",
            "scripts/lock_clients.py",
            "scripts/check_launch_presets.py",
            "scripts/check_shortcuts.py",
            "scripts/check_software_codecs.py",
            "scripts/check_vaapi_padding.py",
            "scripts/analyze_encoder_quality.py",
            "scripts/audit_windows_encoder.py",
            "scripts/benchmark_capture_lifecycle.py",
            "scripts/benchmark_capture_methods.py",
            "scripts/benchmark_gsr_capture.py",
            "scripts/extract_screenshare_*.py",
            "scripts/screenshare_receiver_reports.py",
            "scripts/inspect_voice.py",
            "tests/repro_*.py",
            "tests/capture_adaptation_fixture.py",
            "tests/updater/*-fixture.cjs",
        ),
        ("unit",),
    ),
    (
        ("config/local-runtime.json", "config/runtime-compatibility.json"),
        ("unit", "native", "packaging"),
    ),
    (("scripts/build_capture_shader.py",), ("unit", "native", "packaging", "capture")),
    (("scripts/asar_tool.py",), ("unit", "native", "packaging", "appimage")),
    (("scripts/fixture_storage.py",), ("unit", "native", "packaging")),
    (
        (
            "scripts/appimage.py",
            "scripts/stage_appimage_dependencies.sh",
            ".github/actions/setup-appimage/*",
            "ci/appimage/*",
            "scripts/check_appimage.py",
            "tests/fixtures/appimage-dependencies.py",
            "tests/fixtures/appimage-SHA256SUMS*",
            "config/appimage-tools.json",
        ),
        ("appimage",),
    ),
    ((".github/actions/cache-ci-rust/*",), ("unit",)),
    (
        ("scripts/package_release.py", "packaging/linux/appimage-apprun.sh"),
        ("packaging", "appimage"),
    ),
    (
        (
            "Cargo.toml",
            "Cargo.lock",
            "rust-toolchain.toml",
            ".cargo/*",
            "native/*",
            "scripts/build_rust_support.py",
        ),
        ("unit", "native", "camera", "capture", "packaging", "audio"),
    ),
    (
        (
            "README.md",
            "CONTRIBUTING.md",
            "AGENTS.md",
            "CHANGELOG.md",
            "CODE_OF_CONDUCT.md",
            "SECURITY.md",
            "SUPPORT.md",
            "docs/*.md",
            "docs/*.png",
            "docs/*.svg",
            "docs/*.webp",
            ".github/*.md",
            "agents/skills/*/SKILL.md",
        ),
        (),
    ),
    (("tests/test_*.py", "tests/mod_fixture.py", "tests/*/*.test.cjs"), ("unit",)),
    (
        (
            "scripts/releases.py",
            "scripts/publish_working_tree.py",
            "scripts/release_proposals.py",
            "scripts/publish_aur.py",
            "scripts/release_signing.py",
            "scripts/discover.py",
            "scripts/schedule_updates.py",
            "scripts/update_policy.py",
            "scripts/state_branch.py",
            "scripts/advance_electron.py",
            "scripts/blacksmith_budget.py",
            "scripts/blacksmith_usage.py",
            "scripts/check_runtime_key.py",
            ".github/actions/blacksmith-usage/*",
            "scripts/ci_usage.py",
            "scripts/check_style.py",
            "config/approved-release.json",
            "config/update-policy.json",
            "config/release-notes.json",
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
            "integration/discord-ui/*",
            "tests/discord-ui/*",
            "scripts/patch_app_audio.py",
            "scripts/build_ui.py",
            "scripts/patch_pulse_audio.py",
            "scripts/patch_pulse_reconnect.py",
            "scripts/desktop_identity.py",
        ),
        ("packaging", "audio"),
    ),
    (("scripts/check_app_audio.py", "scripts/check_wayland_audio.py"), ("audio",)),
    (("integration/video/kms-README.md",), ("packaging", "capture")),
    (("integration/video/capture-rate.h",), ("packaging", "native", "camera", "capture")),
    (
        (
            "integration/video/wayland-*",
            "integration/video/vkcapture-*",
            "integration/video/kms-*",
            "integration/video/direct-*",
            "integration/video/screen-preview.c",
            "integration/video/gamecapture-launch.py",
            "integration/video/gamecapture-command.py",
            "integration/video/rebuild-game-consumers.py",
            "integration/video/protocols/*",
            "scripts/build_wayland_capture.py",
            "scripts/build_wayland_source.py",
            "scripts/build_vkcapture_backend.py",
            "scripts/build_kms_source.py",
            "scripts/build_direct_capture.py",
            "scripts/build_screen_preview.py",
            "scripts/stage_vkcapture.py",
            "scripts/stage_capture_backends.py",
            "patches/capture/*",
        ),
        ("packaging", "native", "capture"),
    ),
    (
        (
            "integration/video/capture.c",
            "integration/video/capture-scale.c",
            "integration/video/capture-sync.c",
            "integration/video/capture-upstream.c",
            "integration/video/encoder.c",
            "integration/video/av1-software-crop.c",
            "native/encoder/*",
            "scripts/build_hevc_support.py",
            "integration/video/*.h",
            "scripts/encoder_scope.py",
            "scripts/capture_scale_scope.py",
            "scripts/capture_sync_scope.py",
            "scripts/capture_picker_scope.py",
            "scripts/capture_direct_scope.py",
            "scripts/capture_upstream_scope.py",
        ),
        ("packaging", "camera", "native"),
    ),
    (
        (
            "tests/screenshare/*",
            "scripts/check_screenshare.py",
            "scripts/build_capture_fixture.py",
            "scripts/check_vaapi_backend.py",
            "scripts/benchmark_screenshare.py",
            "scripts/benchmark_screenshare_load.py",
            "scripts/analyze_screenshare.py",
        ),
        ("unit", "native"),
    ),
    (
        (
            "scripts/patch_krisp.py",
            "scripts/patch_vaapi_hrd.py",
            "scripts/native_client_2444.py",
            "scripts/native_client_2472.py",
            "scripts/native_client_2483.py",
            "scripts/native_client_2484.py",
            "scripts/native_client_2510.py",
            "scripts/patch_capture_dimensions.py",
            "scripts/patch_capture_chroma.py",
            "scripts/patch_presentation_clock.py",
            "scripts/patch_video_pacing.py",
            "scripts/patch_decoder_eagain.py",
            "scripts/stage_vaapi_hrd_fix.py",
            "integration/video/*",
            "scripts/client_native.py",
            "scripts/native_elf.py",
            "packaging/upstream/krisp-patcher.py",
        ),
        ("packaging", "native"),
    ),
    (
        (
            "integration/camera/*",
            "scripts/build_camera_support.py",
            "scripts/camera_scope.py",
            "scripts/capture_scope.py",
            "scripts/encoder_scope.py",
            "tests/camera/*",
            "scripts/check_camera_startup.py",
        ),
        ("packaging", "camera"),
    ),
    (("integration/launcher/*",), ("packaging",)),
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
            "scripts/check_desktop_launch.py",
            "scripts/check_working_tree.py",
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
    suites = set(SUITES) if reason else set()
    full_lint = bool(reason)
    pr_reusable = not reason
    documents = []
    decisions = {}
    for path in sorted(set(paths)):
        scopes = next(
            (scopes for patterns, scopes in RULES if any(fnmatchcase(path, p) for p in patterns)),
            SCOPES,
        )
        selected.update(scopes)
        decisions[path] = list(scopes)
        if path.startswith((".github/workflows/", ".github/actions/")) or path in {
            ".github/actionlint.yaml",
            "scripts/ci_scope.py",
            "scripts/ci_pr_evidence.py",
        }:
            pr_reusable = False
        if scopes:
            suites.update(
                next(
                    (
                        suites
                        for patterns, suites in FOCUSED_SUITES
                        if any(fnmatchcase(path, p) for p in patterns)
                    ),
                    SUITES,
                )
            )
            full_lint |= path != "config/release-notes.json"
        elif path.endswith(".md"):
            documents.append(path)
    if suites - {"notes"}:
        selected.add("unit")
    else:
        selected.discard("unit")
    if "python" in suites:
        suites.difference_update(("source_tests", "release_tests"))
    return {
        "checks": {name: name in selected for name in SCOPES},
        "suites": sorted(suites),
        "full_lint": full_lint,
        "pr_reusable": pr_reusable,
        "documents": documents,
        "reason": reason or "Selected from changed paths.",
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
            for name in (*SUITES, "notes", "source_tests", "release_tests"):
                stream.write(f"{name}={str(name in result['suites']).lower()}\n")
            stream.write(f"full_lint={str(result['full_lint']).lower()}\n")
            stream.write(f"pr_reusable={str(result['pr_reusable']).lower()}\n")
            integrations = [scope for scope in PR_INTEGRATIONS if result["checks"][scope]]
            stream.write(f"pr_integrations={json.dumps(integrations)}\n")
            # Do not send a large list when full lint already covers these files.
            documents = result["documents"] if not result["full_lint"] else []
            stream.write(f"documents={json.dumps(documents)}\n")
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a") as stream:
            stream.write("## Check scope\n\n" + result["reason"] + "\n\n")
            stream.write("| Check | Selected |\n| --- | --- |\n")
            lint = "source" if result["full_lint"] else "Markdown" if result["documents"] else "no"
            stream.write(f"| lint | {lint} |\n")
            stream.write(f"| suites | {', '.join(result['suites']) or 'none'} |\n")
            for name, enabled in result["checks"].items():
                if name != "unit":
                    stream.write(f"| {name} | {'yes' if enabled else 'no'} |\n")
            stream.write("\nThe selector log lists the scope for each changed path.\n")


if __name__ == "__main__":
    main()
