"""Check Discord video popouts on private compositors, without an account."""

import argparse
import json
import os
import select
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from common import ROOT, sha256, write_json


def check(
    runtime: Path,
    core: Path | None,
    output: Path,
    preferences: dict,
    *,
    placement=False,
    x11=False,
    scale=1.0,
    compositor="niri",
    tools_prefix: Path | None = None,
    x11_wm=False,
    animations=False,
    second_output: str | None = None,
    panel=False,
):
    runtime, output = runtime.resolve(), output.resolve()
    core = core.resolve() if core else None
    if output.exists() or output.is_relative_to(runtime.parent):
        raise ValueError("Choose a fresh output outside the runtime distribution")
    output.mkdir(parents=True)
    subprocess.run(
        ["cp", "-a", "--reflink=auto", str(runtime.parent), str(output / "runtime")], check=True
    )
    resources = output / "runtime/resources"
    if resources.is_symlink():
        resources.unlink()
    elif resources.exists():
        shutil.rmtree(resources)
    entry = resources / "app"
    entry.mkdir(parents=True)
    write_json(entry / "package.json", {"name": "dpe-popout-fixture", "main": "index.cjs"})
    fixture = ROOT / "tests/popout" / ("placement.cjs" if placement else "main.cjs")
    (entry / "index.cjs").write_text(f"require({json.dumps(str(fixture))});\n")
    if placement and compositor == "gnome":
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=360x640:r=5",
                "-t",
                "2",
                "-c:v",
                "libvpx",
                "-an",
                str(output / "portrait.webm"),
            ],
            check=True,
        )
    children, logs = [], []
    with tempfile.TemporaryDirectory(prefix="dpe-pip-") as temporary:
        run = Path(temporary)
        env = {
            "PATH": os.defpath,
            "LANG": "C.UTF-8",
            "HOME": str(output / "home"),
            "XDG_CONFIG_HOME": str(output / "config"),
            "XDG_CACHE_HOME": str(output / "cache"),
            "XDG_RUNTIME_DIR": str(run),
            "DBUS_SESSION_BUS_ADDRESS": f"unix:path={run}/no-bus",
            "DBUS_SYSTEM_BUS_ADDRESS": f"unix:path={run}/no-system",
            "PULSE_SERVER": f"unix:{run}/no-pulse",
            "LIBGL_ALWAYS_SOFTWARE": "1",
            "DPE_POPOUT_OUTPUT": str(output / "result"),
            "DPE_POPOUT_CORE": str(core),
            "DPE_POPOUT_PREFS": json.dumps(preferences),
            "DPE_POPOUT_TRACE_FILE": str(output / "trace.jsonl"),
            "DPE_POPOUT_X11_WM": "1" if x11_wm else "0",
        }

        if tools_prefix:
            tools_prefix = tools_prefix.resolve()
            env["PATH"] = f"{tools_prefix / 'bin'}:{os.defpath}"
            env["LD_LIBRARY_PATH"] = str(tools_prefix / "lib")
            env["XDG_DATA_DIRS"] = f"{tools_prefix / 'share'}:/usr/share"
        env["DPE_POPOUT_BACKEND"] = "x11" if x11 else compositor

        Path(env["HOME"]).mkdir(parents=True)

        def spawn(args, name, pipe=False):
            log = (output / f"{name}.log").open("w")
            logs.append(log)
            child = subprocess.Popen(
                args,
                env=env,
                stdout=subprocess.PIPE if pipe else log,
                stderr=log,
                start_new_session=True,
            )
            children.append(child)
            return child

        try:
            # Xvfb allocates a free display; never inherit or query the user's one.
            xvfb = spawn(
                ["Xvfb", "-displayfd", "1", "-screen", "0", "1600x1000x24", "-nolisten", "tcp"],
                "xvfb",
                pipe=True,
            )
            if not select.select([xvfb.stdout], [], [], 10)[0]:
                raise RuntimeError("Xvfb did not allocate a display")
            env["DISPLAY"] = ":" + xvfb.stdout.readline().decode().strip()
            if not x11 and compositor == "niri":
                config = output / "niri.kdl"
                base_config = (ROOT / "tests/wayland/niri.kdl").read_text()
                if animations:
                    base_config = base_config.replace("animations { off; }", "")
                config.write_text(
                    base_config
                    + f'\noutput "winit" {{ scale {scale}; }}\n'
                    + "\nlayout { gaps 24; struts { top 30; bottom 40; left 20; right 50; }; border { on; width 4; }; }\n"
                )
                niri = spawn(["niri", "-c", str(config)], "niri")
                deadline = time.monotonic() + 15
                while True:
                    sockets = list(run.glob("niri*.sock"))
                    wayland = [p for p in run.glob("wayland-*") if p.is_socket()]
                    if sockets and wayland:
                        break
                    if time.monotonic() >= deadline or niri.poll() is not None:
                        raise RuntimeError("Private compositor failed; inspect niri.log")
                    time.sleep(0.1)
                env.pop("DISPLAY")
                env.update(
                    NIRI_SOCKET=str(sockets[0]),
                    WAYLAND_DISPLAY=wayland[0].name,
                    XDG_SESSION_TYPE="wayland",
                )
                if panel:
                    config = output / "panel.json"
                    config.write_text(
                        json.dumps(
                            {
                                "layer": "top",
                                "position": "top",
                                "height": 64,
                                "exclusive": True,
                                "modules-left": [],
                                "modules-right": [],
                            }
                        )
                    )
                    style = output / "panel.css"
                    style.write_text("* { min-height: 0; } window#waybar { background: #334455; }")
                    bar = spawn(["waybar", "-c", str(config), "-s", str(style)], "panel")
                    time.sleep(0.4)
                    if bar.poll() is not None:
                        raise RuntimeError("Private layer-shell panel failed; inspect panel.log")
                    env["DPE_POPOUT_PANEL"] = "64"
            elif not x11 and compositor == "sway":
                config = output / "sway.conf"
                config.write_text(
                    f"output * mode 1600x1000 scale {scale}\n"
                    "seat seat0 fallback true\n"
                    "default_border pixel 4\n"
                    "gaps inner 24\n"
                    "floating_minimum_size 1 x 1\n"
                    "focus_on_window_activation focus\n"
                    "xwayland disable\n"
                    "bar {\n position bottom\n height 48\n font pango:monospace 10\n}\n"
                )
                env.update(WLR_BACKENDS="x11", WLR_RENDERER="pixman", WLR_LIBINPUT_NO_DEVICES="1")
                sway = spawn(["sway", "-c", str(config)], "sway")
                deadline = time.monotonic() + 15
                while True:
                    sockets = list(run.glob("sway-ipc*.sock"))
                    wayland = [p for p in run.glob("wayland-*") if p.is_socket()]
                    if sockets and wayland:
                        break
                    if time.monotonic() >= deadline or sway.poll() is not None:
                        raise RuntimeError("Private Sway failed; inspect sway.log")
                    time.sleep(0.1)
                env.pop("DISPLAY")
                env.update(
                    SWAYSOCK=str(sockets[0]),
                    WAYLAND_DISPLAY=wayland[0].name,
                    XDG_SESSION_TYPE="wayland",
                )
                if second_output:
                    subprocess.run(
                        ["swaymsg", "create_output"], env=env, capture_output=True, check=True
                    )
                    outputs = json.loads(
                        subprocess.check_output(["swaymsg", "-r", "-t", "get_outputs"], env=env)
                    )
                    name = outputs[-1]["name"]
                    subprocess.run(
                        [
                            "swaymsg",
                            f"output {name} mode {second_output} scale 1.5 position -1280 200",
                        ],
                        env=env,
                        capture_output=True,
                        check=True,
                    )
                    env["DPE_POPOUT_SECOND_OUTPUT"] = name
            elif not x11 and compositor in ("kwin", "gnome", "hyprland"):
                daemon = spawn(
                    ["dbus-daemon", "--session", "--nofork", "--print-address=1"], "dbus", pipe=True
                )
                if not select.select([daemon.stdout], [], [], 5)[0]:
                    raise RuntimeError("Private session bus did not start")
                env["DBUS_SESSION_BUS_ADDRESS"] = daemon.stdout.readline().decode().strip()
                env["DBUS_SYSTEM_BUS_ADDRESS"] = env["DBUS_SESSION_BUS_ADDRESS"]
                socket_name = "wayland-" + compositor
                if compositor in ("kwin", "hyprland"):
                    env.pop("LIBGL_ALWAYS_SOFTWARE", None)
                    env.update(
                        XDG_CURRENT_DESKTOP="KDE",
                        QT_QPA_PLATFORM="xcb",
                        KWIN_COMPOSE="O2",
                        QT_LOGGING_TO_CONSOLE="1",
                    )
                    command = [
                        "kwin_wayland",
                        "--virtual",
                        "--socket",
                        socket_name,
                        "--width",
                        "1600",
                        "--height",
                        "1000",
                        "--no-lockscreen",
                        "--no-global-shortcuts",
                        "--no-kactivities",
                    ]
                else:
                    env.update(
                        XDG_CURRENT_DESKTOP="GNOME", MUTTER_DEBUG_DUMMY_MODE_SPECS="1600x1000"
                    )
                    command = [
                        "gnome-shell",
                        "--headless",
                        "--virtual-monitor",
                        "1600x1000",
                        "--wayland",
                        "--no-x11",
                        "--wayland-display",
                        socket_name,
                    ]
                wm = spawn(command, "kwin-parent" if compositor == "hyprland" else compositor)
                deadline = time.monotonic() + 20
                while not (run / socket_name).is_socket():
                    if time.monotonic() >= deadline or wm.poll() is not None:
                        raise RuntimeError(f"Private {compositor} failed; inspect its log")
                    time.sleep(0.1)
                if compositor == "gnome":
                    # Finish the private Shell's startup overview transition before
                    # creating clients; do not interpret that blur as a user action.
                    deadline = time.monotonic() + 15
                    while True:
                        reply = subprocess.run(
                            [
                                "gdbus",
                                "call",
                                "--session",
                                "--dest",
                                "org.gnome.Shell",
                                "--object-path",
                                "/org/gnome/Shell",
                                "--method",
                                "org.freedesktop.DBus.Properties.Set",
                                "org.gnome.Shell",
                                "OverviewActive",
                                "<false>",
                            ],
                            env=env,
                            capture_output=True,
                        )
                        if reply.returncode == 0:
                            break
                        if time.monotonic() >= deadline:
                            raise RuntimeError("Private GNOME Shell did not become ready")
                        time.sleep(0.1)
                    time.sleep(1)
                env.pop("DISPLAY")
                env.update(WAYLAND_DISPLAY=socket_name, XDG_SESSION_TYPE="wayland")
                if compositor == "hyprland":
                    config = output / "hyprland.lua"
                    config.write_text(
                        f'hl.monitor({{output="",mode="1600x1000@60",position="auto",scale={scale},reserved_area={{top=30,right=20,bottom=40,left=10}}}})\n'
                        "hl.config({general={gaps_in=12,gaps_out=24,float_gaps=24,border_size=4},"
                        "animations={enabled=false},misc={disable_hyprland_logo=true,"
                        "disable_splash_rendering=true},debug={enable_stdout_logs=true}})\n"
                    )
                    parent_socket = env["WAYLAND_DISPLAY"]
                    env["HYPRLAND_NO_SD_NOTIFY"] = "1"
                    hypr = spawn(["Hyprland", "-c", str(config)], "hyprland")
                    deadline = time.monotonic() + 20
                    while True:
                        ipc = list(run.glob("hypr/*/.socket.sock"))
                        wayland = [
                            p
                            for p in run.glob("wayland-*")
                            if p.is_socket() and p.name != parent_socket
                        ]
                        if ipc and wayland:
                            break
                        if time.monotonic() >= deadline or hypr.poll() is not None:
                            raise RuntimeError("Private Hyprland failed; inspect hyprland.log")
                        time.sleep(0.1)
                    env["XDG_CURRENT_DESKTOP"] = "Hyprland"
                    env.pop("NIRI_SOCKET", None)
                    env.update(
                        HYPRLAND_INSTANCE_SIGNATURE=ipc[0].parent.name,
                        WAYLAND_DISPLAY=wayland[0].name,
                    )
            elif x11_wm:
                wm = spawn(["openbox", "--sm-disable"], "openbox")
                deadline = time.monotonic() + 10
                while True:
                    result = subprocess.run(
                        ["xprop", "-root", "_NET_SUPPORTING_WM_CHECK"],
                        env=env,
                        capture_output=True,
                        text=True,
                    )
                    if "window id #" in result.stdout:
                        break
                    if time.monotonic() >= deadline or wm.poll() is not None:
                        raise RuntimeError("Private Openbox failed; inspect openbox.log")
                    time.sleep(0.1)
            process = spawn(
                [
                    str(output / "runtime" / runtime.name),
                    "--ozone-platform=x11" if x11 else "--ozone-platform=wayland",
                    "--disable-gpu",
                    "--disable-dev-shm-usage",
                ],
                "electron",
            )
            code = process.wait(timeout=70)
            report_path = output / "result/report.json"
            report = json.loads(report_path.read_text())
            report.update(
                executable_sha256=sha256(runtime),
                core_sha256=sha256(core) if core else None,
                display_backend="x11" if x11 else compositor,
                x11_wm=x11_wm,
                animations=animations,
                second_output=second_output,
                scale=scale,
                panel=panel,
                fixture=str(fixture.relative_to(ROOT)),
                process_exit=code,
                preferences=preferences,
            )
            write_json(report_path, report)
            if code or not report["passed"]:
                raise RuntimeError(report.get("error", "Popout fixture failed"))
            print(f"Passed {len(report['cases'])} popout checks: {report_path}")
        finally:
            for child in reversed(children):
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGTERM)
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid, signal.SIGKILL)
                        child.wait()
            for log in logs:
                log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runtime", type=Path)
    parser.add_argument("--core", type=Path, help="Extracted desktop-core bundle.js")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--no-minimum-size", action="store_true")
    parser.add_argument("--no-auto-position", action="store_true")
    parser.add_argument(
        "--panel",
        action="store_true",
        help="Reserve 64 logical pixels using a private layer-shell Waybar",
    )
    parser.add_argument(
        "--corner",
        choices=["top-left", "top-right", "bottom-left", "bottom-right"],
        default="bottom-right",
    )
    parser.add_argument(
        "--placement-only", action="store_true", help="Synthetic geometry and delayed-media checks"
    )
    parser.add_argument(
        "--x11", action="store_true", help="Use private Xvfb instead of native Wayland"
    )
    parser.add_argument(
        "--scale",
        type=float,
        choices=[1.0, 1.25, 1.5, 2.0],
        default=1.0,
        help="Private Niri/Hyprland output scale",
    )
    parser.add_argument(
        "--compositor", choices=["niri", "sway", "hyprland", "kwin", "gnome"], default="niri"
    )
    parser.add_argument(
        "--tools-prefix", type=Path, help="Private tools prefix containing bin/, lib/, share/"
    )
    parser.add_argument("--x11-wm", action="store_true", help="Start private Openbox on Xvfb")
    parser.add_argument("--animations", action="store_true", help="Keep Niri animations enabled")
    parser.add_argument(
        "--second-output",
        nargs="?",
        const="1920x1080",
        choices=["1920x1080", "640x480"],
        help="Create a negative-origin, mixed-scale Sway output",
    )
    args = parser.parse_args()
    if args.x11_wm and not args.x11:
        parser.error("--x11-wm requires --x11")
    if args.panel and (args.x11 or args.compositor != "niri"):
        parser.error("--panel requires native Niri")
    if args.second_output and (args.x11 or args.compositor != "sway"):
        parser.error("--second-output requires native Sway")
    if not args.placement_only and not args.core:
        parser.error("--core is required unless --placement-only is used")
    check(
        args.runtime,
        args.core,
        args.output,
        {
            "minimumWindowSize": not args.no_minimum_size,
            "popoutAutoPosition": not args.no_auto_position,
            "popoutCorner": args.corner,
        },
        placement=args.placement_only,
        x11=args.x11,
        scale=args.scale,
        compositor=args.compositor,
        tools_prefix=args.tools_prefix,
        x11_wm=args.x11_wm,
        animations=args.animations,
        second_output=args.second_output,
        panel=args.panel,
    )


if __name__ == "__main__":
    main()
