"""Launch a staged Discord app with a fresh profile and verify its login window.

No login, call, message, screenshot, webcam or microphone capture. DevTools binds
only to loopback and lives for the duration of this local smoke check.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request

from common import read_json, write_json


def check(app: Path, output: Path, profile: Path | None = None):
    app, output = app.resolve(), output.resolve()
    if output.exists():
        raise ValueError("Choose a fresh report directory")
    output.mkdir(parents=True)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    profile = profile or output / 'profile'
    environment = {**os.environ, "DISCORD_USER_DATA_DIR": str(profile),
        "WAYLAND_DEBUG":"client"}
    environment.pop("ELECTRON_RUN_AS_NODE", None)
    report = {"passed":False, "profile":str(profile), "screenshots_saved":False,
        "real_discord_call_verified":False, "app":str(app), "targets":[]}
    channel = read_json(app / "local-build.json")["channel"]
    host = {"stable":"discord.com", "ptb":"ptb.discord.com", "canary":"canary.discord.com"}[channel]
    with (output / "console.log").open("w") as log:
        process = subprocess.Popen([str(app / "launch.sh"), "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={port}"], env=environment, stdout=log,
            stderr=subprocess.STDOUT, start_new_session=True)
        try:
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline and process.poll() is None:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as response:
                        targets = json.load(response)
                    report["targets"] = [{k:t.get(k) for k in ("type", "url", "title")} for t in targets]
                    for target in targets:
                        if target.get("type") != "page" or not target.get("url", "").startswith(f"https://{host}/"):
                            continue
                        script = r'''
const ws = new WebSocket(process.argv[1]);
const timer = setTimeout(() => {ws.close(); process.exit(2)}, 5000);
ws.addEventListener("open", () => ws.send(JSON.stringify({id:1, method:"Runtime.evaluate", params:{
 expression:'JSON.stringify({ready:document.readyState,title:document.title,loginForm:!!document.querySelector("input[name=email]"),passwordField:!!document.querySelector("input[type=password]")})', returnByValue:true}})));
ws.addEventListener("message", event => { const value=JSON.parse(event.data); if(value.id!==1)return;
 clearTimeout(timer); console.log(value.result?.result?.value || "{}"); ws.close(); });
ws.addEventListener("error", () => process.exit(3));
'''
                        result = subprocess.run(["node", "--input-type=module", "-e", script,
                            target["webSocketDebuggerUrl"]], capture_output=True, text=True, timeout=8)
                        if result.returncode == 0:
                            page = json.loads(result.stdout)
                            report["page"] = page
                            if page.get("ready") == "complete" and page.get("loginForm") and page.get("passwordField"):
                                report["passed"] = True
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
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as response:
                        browser = json.load(response)["webSocketDebuggerUrl"]
                    subprocess.run(["node", "--input-type=module", "-e", '''
const ws = new WebSocket(process.argv[1]);
const timer = setTimeout(() => {ws.close(); process.exit(0)}, 3000);
ws.addEventListener("open", () => ws.send(JSON.stringify({id:1,method:"Browser.close"})));
ws.addEventListener("close", () => {clearTimeout(timer); process.exit(0)});
ws.addEventListener("error", () => process.exit(0));
''', browser], capture_output=True, timeout=5)
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
    console = (output / 'console.log').read_text(errors='replace')
    report["native_wayland_connection"] = "wl_display" in console
    report['uncaught_exception'] = 'Uncaught exception:' in console or 'UnhandledPromiseRejectionWarning' in console
    report['passed'] = report['passed'] and not report['uncaught_exception']
    write_json(output / "launch-report.json", report)
    if report["passed"]:
        manifest = read_json(app / "local-build.json")
        manifest["launch_verified"] = True
        manifest["launch_report"] = str(output / "launch-report.json")
        write_json(app / "local-build.json", manifest)
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument('--relaunch', action='store_true', help='Repeat with the same newly created fixture profile')
    args = parser.parse_args()
    first = check(args.app, args.output)
    passed = first['passed']
    if passed and args.relaunch:
        passed = check(args.app, args.output / 'relaunch', Path(first['profile']))['passed']
    raise SystemExit(0 if passed else 1)
