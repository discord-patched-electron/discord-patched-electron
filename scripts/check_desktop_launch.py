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


def verify_codec(app: Path, profile: Path, browser_pid: int) -> dict | None:
    # Discord's process.title collapses argv into a single string and clears
    # the original environment storage exposed by /proc. Inspect the known
    # managed path in that title, then require the actual mapped library.
    arguments = Path(f"/proc/{browser_pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()
    manifest = app / "integration/updater/openh264.json"
    if (
        not re.search(r"(?:^|\s)--enable-libopenh264(?:=|\s|$)", arguments)
        or not manifest.is_file()
    ):
        return None
    metadata = read_json(manifest)
    library = profile / "dpe-codecs/openh264" / metadata["filename"]
    if f"--openh264-library-path={library}" not in arguments:
        return None
    mapped = str(library) in Path(f"/proc/{browser_pid}/maps").read_text().replace("\\040", " ")
    digest = sha256(library)
    if not mapped or digest != metadata["library_sha256"]:
        raise ValueError("OpenH264 was not verified and preloaded")
    return {"sha256": digest, "preloaded": True}


def check(
    app: Path,
    output: Path,
    profile: Path | None = None,
    equicord: bool = False,
    read_only_app: bool = False,
    runtime_archive: Path | None = None,
    application_archive: Path | None = None,
    expect_setup: bool = False,
    setup_configure: bool = False,
    launcher: Path | None = None,
):
    expect_setup = expect_setup or setup_configure
    app, output = app.resolve(), output.resolve()
    if output.exists():
        raise ValueError("Choose a fresh report directory")
    output.mkdir(parents=True)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    profile = (profile or output / "profile").resolve()
    launcher_app = app
    launch_environment = dict(os.environ)
    if launcher and (app / "bootstrap.json").exists():
        launch_environment["APPIMAGE"] = str(launcher.resolve())
        launch_environment["APPDIR"] = str(app.parents[2])
    if (app / "bootstrap.json").exists():
        command = ["python3", "-I", str(app / "loader.py"), "--profile", str(profile), "--resolve"]
        if application_archive:
            command += ["--archive", str(application_archive.resolve())]
        app = Path(subprocess.check_output(command, text=True, env=launch_environment).strip())
        read_only_app = True
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
        **launch_environment,
        "DPE_PROFILE_DIR": str(profile),
        "EQUICORD_USER_DATA_DIR": str(profile / "equicord"),
        "VENCORD_USER_DATA_DIR": str(profile / "vencord"),
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
        "setup_expected": expect_setup,
        "setup_seen": False,
        "setup_configure_expected": setup_configure,
        "client_before_done": False,
    }
    channel = read_json(app / "local-build.json")["channel"]
    host = {"stable": "discord.com", "ptb": "ptb.discord.com", "canary": "canary.discord.com"}[
        channel
    ]
    with (output / "console.log").open("w") as log:
        process = subprocess.Popen(
            [
                str(launcher.resolve() if launcher else launcher_app / "launch.sh"),
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
            while time.monotonic() < deadline and (
                process.poll() is None or report.get("setup_done_sent")
            ):
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/json/list", timeout=2
                    ) as response:
                        targets = json.load(response)
                    report["targets"] = [
                        {k: t.get(k) for k in ("type", "url", "title")} for t in targets
                    ]
                    if any(t.get("url") == "dpe-splash://startup/index.html" for t in targets):
                        report["updater_ui_seen"] = True
                    welcome_target = next(
                        (
                            t
                            for t in targets
                            if t.get("url") == "dpe-splash://welcome/config.html?mode=welcome"
                        ),
                        None,
                    )
                    if welcome_target:
                        report["setup_seen"] = True
                    configure_target = next(
                        (t for t in targets if t.get("url") == "dpe-splash://app/config.html"),
                        None,
                    )
                    discord_pages = []
                    for target in targets:
                        if target.get("type") != "page" or not target.get("url", "").startswith(
                            f"https://{host}/"
                        ):
                            continue
                        page = inspect_discord(target["webSocketDebuggerUrl"])
                        if page["discordNative"]:
                            discord_pages.append((page, target["webSocketDebuggerUrl"]))
                        else:
                            report["presentation_helper_seen"] = True
                    verify_setup = expect_setup or report["setup_seen"]
                    if verify_setup and not report.get("setup_done_sent"):
                        if discord_pages:
                            report["client_before_done"] = True
                            break
                        if welcome_target:
                            report["setup_gated"] = True
                    if welcome_target and not (
                        report.get("setup_done_sent") or report.get("setup_configure_sent")
                    ):
                        setup = complete_setup(
                            welcome_target["webSocketDebuggerUrl"], configure=setup_configure
                        )
                        if setup:
                            report["setup"] = setup
                            report["setup_choice_sent"] = report.get(
                                "setup_choice_sent", False
                            ) or setup.get("choiceSent", False)
                            report["setup_done_sent"] = setup.get("doneSent", False)
                            report["setup_configure_sent"] = setup.get("configureSent", False)
                    if (
                        setup_configure
                        and report.get("setup_configure_sent")
                        and not report.get("setup_done_sent")
                        and welcome_target is None
                        and configure_target is not None
                    ):
                        configured = close_setup_settings(configure_target["webSocketDebuggerUrl"])
                        report["configure"] = configured
                        if configured.get("closeSent"):
                            report["welcome_closed_before_configure_finish"] = True
                            report["setup_done_sent"] = True
                    marker = profile / "dpe-updater/mods.json"
                    if report.get("setup_done_sent") and marker.exists():
                        report["setup_complete"] = (
                            read_json(marker).get("setupComplete") is True
                            and welcome_target is None
                            and (not setup_configure or configure_target is None)
                        )
                    for page, websocket in discord_pages:
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
                                report["passed"] = False
                                with urllib.request.urlopen(
                                    f"http://127.0.0.1:{port}/json/version", timeout=2
                                ) as response:
                                    browser = json.load(response)["webSocketDebuggerUrl"]
                                processes = cdp(browser, "SystemInfo.getProcessInfo")
                                browser_pid = next(
                                    (
                                        int(item["id"])
                                        for item in processes.get("processInfo", [])
                                        if item["type"] == "browser"
                                    ),
                                    None,
                                )
                                if browser_pid is None:
                                    raise ValueError("Could not identify the browser process")
                                report["runtime_executable"] = str(
                                    Path(f"/proc/{browser_pid}/exe").resolve(strict=True)
                                )
                                if codec := verify_codec(app, profile, browser_pid):
                                    report["openh264"] = codec
                                report["popout"] = check_popout(websocket)
                                report["passed"] = report["popout"].get("passed") is True
                                break
                    if "popout" in report and not report["passed"]:
                        break
                    if report["passed"] and (not verify_setup or report.get("setup_complete")):
                        break
                except (OSError, ValueError, subprocess.SubprocessError, urllib.error.URLError):
                    pass
                time.sleep(1)
            report["exited_before_check"] = process.poll()
        finally:
            # Ask Chromium to close before terminating its process tree, so the
            # smoke check does not itself manufacture GPU/zygote crash reports.
            if process.poll() is None or report.get("setup_done_sent"):
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
                    # Done relaunches through the launcher, so the original Popen
                    # may have exited before this browser releases its profile.
                    closing_deadline = time.monotonic() + 8
                    while time.monotonic() < closing_deadline:
                        try:
                            with urllib.request.urlopen(
                                f"http://127.0.0.1:{port}/json/version", timeout=1
                            ):
                                pass
                        except OSError:
                            break
                        time.sleep(0.1)
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
    if expect_setup or report["setup_seen"]:
        report["passed"] = (
            report["passed"]
            and report.get("setup_complete", False)
            and report.get("setup_gated", False)
            and not report["client_before_done"]
        )
    if setup_configure:
        report["passed"] = (
            report["passed"]
            and report.get("setup_configure_sent", False)
            and report.get("welcome_closed_before_configure_finish", False)
        )
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
        report["module_api_verified"] = (
            report.get("page", {}).get("moduleVersions")
            == {name: item["version"] for name, item in state["modules"].items()}
            and report.get("page", {}).get("nativeUpdater") is True
        )
        report["passed"] = (
            report["passed"] and report["client_pins_verified"] and report["module_api_verified"]
        )
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


def inspect_discord(websocket: str) -> dict:
    """Distinguish the desktop client from the isolated presentation helper."""
    expression = r"""JSON.stringify({
      ready:document.readyState, title:document.title,
      loginForm:!!document.querySelector('input[name=email]'),
      passwordField:!!document.querySelector('input[type=password]'),
      equicordNative:typeof VencordNative!=='undefined',
      equicordRenderer:typeof Vencord!=='undefined'&&!!Vencord.Plugins,
      discordNative:typeof DiscordNative!=='undefined',
      moduleVersions:globalThis.DiscordNative?.app?.getModuleVersions?.(),
      nativeUpdater:globalThis.DiscordNative?.nativeModules?.hasUpdater
        ?? (globalThis.DiscordNative?.nativeModules?.canBootstrapNewUpdater === false),
      modSelfUpdate:globalThis.__DPE_MOD_SELF_UPDATE__
    })"""
    result = cdp(websocket, "Runtime.evaluate", {"expression": expression, "returnByValue": True})
    page = json.loads(result.get("result", {}).get("value", "{}"))
    if not isinstance(page, dict) or not isinstance(page.get("discordNative"), bool):
        # A failed inspection is unknown, not proof that the desktop is absent.
        # The caller retries before advancing either onboarding action.
        raise ValueError("Could not classify the Discord target")
    return page


def check_popout(websocket: str) -> dict:
    """Exercise Discord's real popout handler and loaded handshake at the login page.

    The parent writes only a synthetic marker, never account or voice data.
    Use each real video-popout name twice to verify that closing releases the registry.
    """
    expression = r"""(async () => {
      if (!document.querySelector('input[name=email]')
          || !document.querySelector('input[type=password]')) {
        return {passed:false, error:'Popout check requires the fresh login page'};
      }
      const attempts = [];
      const keys = ['DISCORD_CHANNEL_CALL_POPOUT', 'DISCORD_CHANNEL_CALL_POPOUT',
        'DISCORD_CALL_TILE_POPOUT_dpe_fixture', 'DISCORD_CALL_TILE_POPOUT_dpe_fixture'];
      for (const key of keys) {
        const result = await new Promise(resolve => {
          let child, timer;
          const finish = value => {
            clearTimeout(timer);
            window.removeEventListener('message', loaded);
            if (child && !child.closed) child.close();
            resolve(value);
          };
          const loaded = event => {
            if (event.origin !== location.origin || event.source !== child
                || event.data?.discordPopoutEvent?.type !== 'loaded') return;
            try {
              const root = child.document.querySelector('#app-mount');
              const marker = child.document.createElement('div');
              marker.textContent = 'DPE synthetic popout check';
              root?.replaceChildren(marker);
              const state = {
                opener:child.opener === window,
                sameOrigin:child.location.origin === location.origin,
                rendered:root?.firstElementChild === marker,
                nodeDisabled:typeof child.require === 'undefined'
                  && typeof child.process === 'undefined',
                loadedHandshake:true,
              };
              finish({...state, passed:Object.values(state).every(Boolean)});
            } catch (error) { finish({passed:false, error:String(error)}); }
          };
          window.addEventListener('message', loaded);
          timer = setTimeout(() => finish({passed:false, error:'Popout loaded handshake timed out'}), 12000);
          try {
            child = window.open('/popout', key, 'menubar=no,toolbar=no,location=no,directories=no,width=854,height=480,');
            if (!child) finish({passed:false, error:'window.open returned null (request denied or opener unavailable)'});
          } catch (error) { finish({passed:false, error:String(error)}); }
        });
        attempts.push({key, ...result});
        if (!result.passed) break;
        await new Promise(resolve => setTimeout(resolve, 150));
      }
      return {passed:attempts.length === keys.length && attempts.every(item => item.passed), attempts};
    })()"""
    result = cdp(
        websocket,
        "Runtime.evaluate",
        {"expression": expression, "returnByValue": True, "awaitPromise": True},
        timeout=30,
    )
    value = result.get("result", {}).get("value")
    return value if isinstance(value, dict) else {"passed": False, "error": "Popout check failed"}


def close_setup_settings(websocket: str) -> dict:
    """Close the ready onboarding Settings only after Welcome has disappeared."""
    expression = r"""(() => {
      const onboarding = window.Native?.isOnboardingSettings?.() === true;
      const note = document.getElementById('onboarding-close-note');
      const restart = document.getElementById('b_restart');
      const close = document.getElementById('b_close');
      const closeSent = onboarding && document.readyState === 'complete'
        && document.body.dataset.presentationReady === 'true'
        && document.visibilityState === 'visible'
        && !!note?.getClientRects().length
        && note.textContent.trim() === 'Closing settings will launch Discord.'
        && !!restart && !restart.disabled && !!close && !close.disabled;
      if (closeSent) setTimeout(() => close.click(), 250);
      return JSON.stringify({onboarding, closeSent});
    })()"""
    result = cdp(websocket, "Runtime.evaluate", {"expression": expression, "returnByValue": True})
    return json.loads(result.get("result", {}).get("value", "{}"))


def complete_setup(websocket: str, *, configure: bool = False) -> dict:
    """Advance one Welcome page, without a mod; never touch the account page."""
    expression = r"""(() => {
      const welcome = window.Native?.getMode() === 'welcome';
      const next = document.getElementById('setup-next');
      const done = document.getElementById('setup-done');
      const configure = document.getElementById('setup-configure');
      const useConfigure = USE_CONFIGURE;
      const ready = button => welcome && button && !button.disabled
        && document.body.dataset.presentationReady === 'true'
        && document.visibilityState === 'visible'
        && button.getClientRects().length > 0;
      const choiceSent = !!ready(next);
      const configureSent = !choiceSent && useConfigure && !!ready(configure);
      const doneSent = !choiceSent && !useConfigure && !!ready(done);
      if (choiceSent) next.click();
      if (configureSent) configure.click();
      if (doneSent) done.click();
      return JSON.stringify({welcome, choiceSent, configureSent, doneSent});
    })()""".replace("USE_CONFIGURE", json.dumps(configure))
    result = cdp(websocket, "Runtime.evaluate", {"expression": expression, "returnByValue": True})
    return json.loads(result.get("result", {}).get("value", "{}"))


def cdp(websocket: str, method: str, params: dict | None = None, *, timeout: int = 5) -> dict:
    """Perform one bounded DevTools request against this fixture's loopback browser."""
    script = r"""
const ws = new WebSocket(process.argv[1]);
const timer = setTimeout(() => {ws.close(); process.exit(2)}, Number(process.argv[4]) * 1000);
ws.addEventListener("open", () => ws.send(JSON.stringify({id:1, method:process.argv[2], params:JSON.parse(process.argv[3])})));
ws.addEventListener("message", event => { const value=JSON.parse(event.data); if(value.id!==1)return;
 clearTimeout(timer); console.log(JSON.stringify(value.result || {})); ws.close(); });
ws.addEventListener("error", () => process.exit(3));
"""
    result = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            script,
            websocket,
            method,
            json.dumps(params or {}),
            str(timeout),
        ],
        capture_output=True,
        text=True,
        timeout=timeout + 3,
    )
    return json.loads(result.stdout) if result.returncode == 0 else {}


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
    parser.add_argument("--application-archive", type=Path)
    parser.add_argument("--launcher", type=Path, help="Launch an AppImage instead of launch.sh")
    parser.add_argument(
        "--expect-setup",
        action="store_true",
        help="Verify Welcome gates Discord until Restart to finish, and continue without a mod",
    )
    parser.add_argument(
        "--setup-configure",
        action="store_true",
        help="Verify Configure more replaces Welcome and closing Settings starts Discord",
    )
    args = parser.parse_args()
    first = check(
        args.app,
        args.output,
        equicord=args.equicord,
        read_only_app=args.read_only_app,
        runtime_archive=args.runtime_archive,
        application_archive=args.application_archive,
        expect_setup=args.expect_setup,
        setup_configure=args.setup_configure,
        launcher=args.launcher,
    )
    passed = first["passed"]
    if passed and args.relaunch:
        second = check(
            args.app,
            args.output / "relaunch",
            Path(first["profile"]),
            args.equicord,
            args.read_only_app,
            launcher=args.launcher,
        )
        passed = second["passed"] and (
            not (args.expect_setup or args.setup_configure) or not second["setup_seen"]
        )
    raise SystemExit(0 if passed else 1)
