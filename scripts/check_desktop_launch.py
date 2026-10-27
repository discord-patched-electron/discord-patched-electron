"""Launch a staged Discord app with a fresh profile and verify its login window.

No login, call, message, screenshot, webcam or microphone capture. DevTools binds
only to loopback and lives for the duration of this local smoke check.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from common import read_json, sha256, write_json
from desktop_identity import desktop_id


def check(
    app: Path,
    output: Path,
    profile: Path | None = None,
    equicord: bool = False,
    read_only_app: bool = False,
    runtime_archive: Path | None = None,
):
    app, output = app.resolve(), output.resolve()
    if output.exists():
        raise ValueError("Choose a fresh report directory")
    output.mkdir(parents=True)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    profile = (profile or output / "profile").resolve()
    if runtime_archive:
        subprocess.run(
            [
                "python3",
                str(app / "integration/updater/runtime.py"),
                str(app),
                str(profile),
                "--offline",
                "--archive",
                str(runtime_archive.resolve()),
            ],
            env={**os.environ, "DPE_RUNTIME_BUNDLED": "1", "PYTHONDONTWRITEBYTECODE": "1"},
            check=True,
        )
    environment = {
        **os.environ,
        "DPE_PROFILE_DIR": str(profile),
        "EQUICORD_USER_DATA_DIR": str(profile / "equicord"),
        "WAYLAND_DEBUG": "client",
        "DPE_CLIENT_PINNED": "1",
        "DPE_RUNTIME_BUNDLED": "1",
    }
    environment.pop("ELECTRON_RUN_AS_NODE", None)
    # A staged portable app must work from an ordinary terminal, without uv's
    # virtualenv accidentally supplying its runtime Python dependencies.
    environment["PATH"] = os.defpath
    for name in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME"):
        environment.pop(name, None)
    report = {
        "passed": False,
        "profile": str(profile),
        "screenshots_saved": False,
        "real_discord_call_verified": False,
        "app": str(app),
        "targets": [],
        "updater_ui_seen": False,
    }
    channel = read_json(app / "local-build.json")["channel"]
    host = {"stable": "discord.com", "ptb": "ptb.discord.com", "canary": "canary.discord.com"}[
        channel
    ]
    with (output / "console.log").open("w") as log:
        process = subprocess.Popen(
            [
                str(app / "launch.sh"),
                "--remote-debugging-address=127.0.0.1",
                f"--remote-debugging-port={port}",
            ],
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline and process.poll() is None:
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/json/list", timeout=2
                    ) as response:
                        targets = json.load(response)
                    report["targets"] = [
                        {k: t.get(k) for k in ("type", "url", "title")} for t in targets
                    ]
                    if any(t.get("url") == "dpe-splash://app/index.html" for t in targets):
                        report["updater_ui_seen"] = True
                    for target in targets:
                        if target.get("type") != "page" or not target.get("url", "").startswith(
                            f"https://{host}/"
                        ):
                            continue
                        script = r"""
const ws = new WebSocket(process.argv[1]);
const timer = setTimeout(() => {ws.close(); process.exit(2)}, 5000);
ws.addEventListener("open", () => ws.send(JSON.stringify({id:1, method:"Runtime.evaluate", params:{
 expression:'JSON.stringify({ready:document.readyState,title:document.title,loginForm:!!document.querySelector("input[name=email]"),passwordField:!!document.querySelector("input[type=password]"),equicordNative:typeof VencordNative!=="undefined",equicordRenderer:typeof Vencord!=="undefined"&&!!Vencord.Plugins,discordNative:typeof DiscordNative!=="undefined",modSelfUpdate:globalThis.__DPE_MOD_SELF_UPDATE__})', returnByValue:true}})));
ws.addEventListener("message", event => { const value=JSON.parse(event.data); if(value.id!==1)return;
 clearTimeout(timer); console.log(value.result?.result?.value || "{}"); ws.close(); });
ws.addEventListener("error", () => process.exit(3));
"""
                        result = subprocess.run(
                            [
                                "node",
                                "--input-type=module",
                                "-e",
                                script,
                                target["webSocketDebuggerUrl"],
                            ],
                            capture_output=True,
                            text=True,
                            timeout=8,
                        )
                        if result.returncode == 0:
                            page = json.loads(result.stdout)
                            report["page"] = page
                            if (
                                page.get("ready") == "complete"
                                and page.get("loginForm")
                                and page.get("passwordField")
                            ):
                                mod_loaded = bool(
                                    page.get("equicordNative") and page.get("equicordRenderer")
                                )
                                report["passed"] = bool(page.get("discordNative")) and (
                                    mod_loaded
                                    if equicord
                                    else not (
                                        page.get("equicordNative") or page.get("equicordRenderer")
                                    )
                                )
                                if report["passed"]:
                                    report["runtime_executable"] = str(
                                        Path(f"/proc/{process.pid}/exe").resolve(strict=True)
                                    )
                                    break
                    if report["passed"]:
                        break
                except (OSError, ValueError, subprocess.SubprocessError, urllib.error.URLError):
                    pass
                time.sleep(1)
            report["exited_before_check"] = process.poll()
        finally:
            # Ask Chromium to close before terminating its process tree, so the
            # smoke check does not itself manufacture GPU/zygote crash reports.
            if process.poll() is None:
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/json/version", timeout=2
                    ) as response:
                        browser = json.load(response)["webSocketDebuggerUrl"]
                    subprocess.run(
                        [
                            "node",
                            "--input-type=module",
                            "-e",
                            """
const ws = new WebSocket(process.argv[1]);
const timer = setTimeout(() => {ws.close(); process.exit(0)}, 3000);
ws.addEventListener("open", () => ws.send(JSON.stringify({id:1,method:"Browser.close"})));
ws.addEventListener("close", () => {clearTimeout(timer); process.exit(0)});
ws.addEventListener("error", () => process.exit(0));
""",
                            browser,
                        ],
                        capture_output=True,
                        timeout=5,
                    )
                except (OSError, ValueError, KeyError, subprocess.SubprocessError):
                    pass
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
    console = (output / "console.log").read_text(errors="replace")
    report["native_wayland_connection"] = "wl_display" in console
    report["wayland_app_ids"] = sorted(set(re.findall(r'\.set_app_id\("([^\"]+)"\)', console)))
    report["expected_desktop_id"] = desktop_id(channel)
    if report["native_wayland_connection"]:
        report["passed"] = report["passed"] and report["wayland_app_ids"] == [desktop_id(channel)]
    report["equicord_required"] = equicord
    report["uncaught_exception"] = (
        "uncaught exception:" in console.lower()
        or "UnhandledPromiseRejectionWarning" in console
        or "App threw an error during load" in console
    )
    report["passed"] = report["passed"] and not report["uncaught_exception"]
    current = profile / "dpe-updater/current.json"
    if current.exists():
        generation = read_json(current)
        state = read_json(profile / "dpe-updater/generations" / generation / "client.json")
        report["client_version"] = state["version"]
        report["client_generation"] = generation
        report["client_distribution"] = "module-only"
        pinned = read_json(app / "client-update.json")
        report["client_manifest_sha256"] = sha256(app / "client-update.json")
        expected = {
            name: {
                "url": item["full"]["url"],
                "sha256": item["full"]["package_sha256"],
                "version": item["full"]["module_version"],
            }
            for name, item in pinned["modules"].items()
        }
        report["client_pins_verified"] = (
            state["version"] == ".".join(map(str, pinned["full"]["host_version"]))
            and state["modules"] == expected
        )
        report["passed"] = report["passed"] and report["client_pins_verified"]
    write_json(output / "launch-report.json", report)
    if report["passed"] and not read_only_app:
        manifest = read_json(app / "local-build.json")
        manifest["launch_verified"] = True
        manifest["launch_report"] = str(output / "launch-report.json")
        if "client_version" in report:
            manifest["client_generation"] = report["client_generation"]
            manifest["client_pins_verified"] = report["client_pins_verified"]
            manifest["client_manifest_sha256"] = report["client_manifest_sha256"]
        write_json(app / "local-build.json", manifest)
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--runtime-archive",
        type=Path,
        help="Reuse the verified compiler archive for a runtime-free package",
    )
    parser.add_argument(
        "--relaunch", action="store_true", help="Repeat with the same newly created fixture profile"
    )
    parser.add_argument(
        "--equicord",
        action="store_true",
        help="Require the separately installed Equicord preload and renderer",
    )
    parser.add_argument(
        "--read-only-app", action="store_true", help="Do not update the installed build manifest"
    )
    args = parser.parse_args()
    first = check(
        args.app,
        args.output,
        equicord=args.equicord,
        read_only_app=args.read_only_app,
        runtime_archive=args.runtime_archive,
    )
    passed = first["passed"]
    if passed and args.relaunch:
        passed = check(
            args.app,
            args.output / "relaunch",
            Path(first["profile"]),
            args.equicord,
            args.read_only_app,
        )["passed"]
    raise SystemExit(0 if passed else 1)
