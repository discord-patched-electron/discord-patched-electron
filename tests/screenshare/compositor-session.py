"""Own one niri/Mutter monitor-capture session for the bounded native fixture.

Run with the system Python providing dbus-python and PyGObject. No picker,
account, pixels or existing session is inspected. EOF closes this session only.
"""

import json
import selectors
import signal
import sys
import time

import dbus
import dbus.mainloop.glib
from gi.repository import GLib


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Pass the selected monitor name")
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    api = dbus.Interface(
        bus.get_object("org.gnome.Mutter.ScreenCast", "/org/gnome/Mutter/ScreenCast"),
        "org.gnome.Mutter.ScreenCast",
    )
    path = api.CreateSession(dbus.Dictionary({}, signature="sv"), timeout=5)
    session = dbus.Interface(
        bus.get_object("org.gnome.Mutter.ScreenCast", path),
        "org.gnome.Mutter.ScreenCast.Session",
    )
    stopping = False

    def stop(_signal, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    signal.alarm(120)
    selector = selectors.DefaultSelector()
    selector.register(sys.stdin, selectors.EVENT_READ)
    try:
        stream = session.RecordMonitor(
            sys.argv[1], dbus.Dictionary({"cursor-mode": dbus.UInt32(0)}, signature="sv")
        )
        ids = []
        bus.get_object("org.gnome.Mutter.ScreenCast", stream).connect_to_signal(
            "PipeWireStreamAdded",
            lambda value: ids.append(int(value)),
            dbus_interface="org.gnome.Mutter.ScreenCast.Stream",
        )
        session.Start(timeout=5)
        context = GLib.MainContext.default()
        until = time.monotonic() + 8
        while not ids and not stopping and time.monotonic() < until:
            while context.pending():
                context.iteration(False)
            time.sleep(0.01)
        if not ids:
            raise RuntimeError("Compositor did not publish a capture node")
        print(json.dumps({"event": "ready", "nodeId": ids[0]}), flush=True)
        while not stopping:
            while context.pending():
                context.iteration(False)
            if selector.select(0.1):
                break
    finally:
        selector.close()
        session.Stop(timeout=3)


if __name__ == "__main__":
    main()
